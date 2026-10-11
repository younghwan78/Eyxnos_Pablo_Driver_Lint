"""Validated policy, diagnostics, and source views shared by all checks."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath


class LintError(Exception):
    """An operational error: never represent this as a successful lint."""


LEVELS = {"info": 0, "warning": 1, "error": 2}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    rule: str
    severity: str
    message: str
    hint: str = ""
    evidence: str = ""

    def identity(self) -> tuple[str, str, str, str]:
        # Line numbers can move. Evidence, not absolute line position, identifies
        # an existing finding. The runner counts repeated occurrences separately.
        return self.path, self.rule, self.message, self.evidence.strip()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Change:
    path: str
    before: bytes | None
    after: bytes | None
    added_lines: set[int] = field(default_factory=set)
    deleted_lines: list[str] = field(default_factory=list)
    audit: bool = False

    @property
    def new(self) -> bool:
        return self.before is None and not self.audit

    @property
    def deleted(self) -> bool:
        return self.after is None


@dataclass
class Result:
    mode: str
    files: list[str]
    diagnostics: list[Diagnostic]
    existing_count: int = 0
    suppressions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    engines: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def exit_code(self, fail_on: str) -> int:
        return int(any(LEVELS[d.severity] >= LEVELS[fail_on] for d in self.diagnostics))


class Policy:
    def __init__(self, root: Path, path: Path, profile: str | Path | None = None):
        self.root = root.resolve()
        self.path = path.resolve()
        try:
            self.common = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LintError(f"Cannot read policy: {exc}") from exc
        if not isinstance(self.common, dict) or self.common.get("schema_version") != 2:
            raise LintError("Expected common policy schema_version 2 and a separate project profile")
        if any(key in self.common for key in ("target", "scope", "camera_root", "fragment_requirements")):
            raise LintError("Hardware selection belongs in the project profile, not common policy")
        if profile is None:
            selected = self.common.get("profile")
            if not isinstance(selected, str) or not selected:
                raise LintError("Common policy needs a default profile path")
            self.profile_path = (self.path.parent / selected).resolve()
        elif re.fullmatch(r"[A-Za-z0-9_-]+", str(profile)):
            self.profile_path = (self.path.parent / "profiles" / (str(profile) + ".json")).resolve()
        else:
            self.profile_path = self.safe_path(profile)
        # Configuration may be supplied from outside --root (e.g. a shared
        # tool checkout). Source paths inside it are always workspace-relative.
        try:
            self.profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LintError(f"Cannot read project profile: {exc}") from exc
        self._validate_profile()
        self.data = dict(self.common)
        self.data.update({key: self.profile[key] for key in ("scope", "camera_root")})
        self.data["exclude"] = [*self.common["exclude"], *self.profile.get("scope_exclude", [])]
        if "scope_include" in self.profile:
            self.data["scope_include"] = self.profile["scope_include"]
        self.data["target"] = {
            "profile": self.profile["id"], "device": self.profile["device"], "soc": self.profile["soc"],
            "versions": {d["name"]: d["selected_versions"] for d in self.profile["version_domains"]},
        }
        for key in ("scope", "extensions", "filenames", "exclude", "layer_patterns"):
            if not isinstance(self.data.get(key), list) or not self.data[key]:
                raise LintError(f"Policy {key} must be a nonempty list")
        if any(v not in LEVELS for v in self.data.get("severity_overrides", {}).values()):
            raise LintError("Severity overrides must be error, warning, or info")
        if "family_include_pattern" in self.data:
            self._pattern(self.data["family_include_pattern"], "family_include_pattern", capture=True)
        if "reports" in self.data:
            reports = self.safe_path(self.data["reports"])
            if not reports.is_relative_to(self.root / "driver_lint") or self.in_scope(reports.relative_to(self.root).as_posix()):
                raise LintError("Component reports must stay below driver_lint outside source scope")
        for value in ("max_line_length", "max_function_lines", "max_patch_lines"):
            if not isinstance(self.data.get(value), int) or self.data[value] < 1:
                raise LintError(f"Policy {value} must be a positive integer")
        for scope in self.data["scope"]:
            self.safe_path(scope)
        self.safe_path(self.data["camera_root"])
        self.safe_path(self.data["snapshot"])
        self.safe_path(self.data["checkpatch"]["script"])
        self.hash = digest(json.dumps({"policy": self.common, "profile": self.profile},
                                      sort_keys=True, ensure_ascii=False).encode("utf-8"))
        try:
            catalog_path = Path(__file__).resolve().parents[2] / "config" / "rules.json"
            self.rules = {r["id"]: r for r in json.loads(catalog_path.read_text(encoding="utf-8"))}
        except (OSError, ValueError, KeyError) as exc:
            raise LintError(f"Cannot read rule catalog: {exc}") from exc
        overrides = self.data.get("severity_overrides", {})
        if set(overrides) - self.rules.keys():
            raise LintError(f"Unknown severity override: {sorted(set(overrides) - self.rules.keys())}")
        self._validate_suppressions()

    @staticmethod
    def _strings(value, label: str) -> None:
        if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
            raise LintError(f"Profile {label} must be a nonempty string list")

    @staticmethod
    def _pattern(value, label: str, capture: bool = False) -> None:
        try:
            if not isinstance(value, str) or not value:
                raise ValueError("missing regex")
            pattern = re.compile(value)
            if capture and pattern.groups != 1:
                raise ValueError("expected exactly one capturing group for the version")
        except (re.error, ValueError) as exc:
            raise LintError(f"Invalid profile pattern {label}: {exc}") from exc

    def _validate_profile(self) -> None:
        p = self.profile
        if not isinstance(p, dict) or p.get("schema_version") != 1:
            raise LintError("Unsupported project profile schema_version")
        for key in ("id", "device", "soc", "camera_root"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise LintError(f"Profile needs {key}")
        self.safe_path(p["camera_root"])
        component = p.get("component", "combined")
        if component not in ("combined", "pablo-is", "gdc"):
            raise LintError("Unknown driver component")
        if "component" in self.common and self.common["component"] != component:
            raise LintError("Policy and HW profile belong to different driver components")
        self.component = component
        self._strings(p.get("scope"), "scope")
        for path in p["scope"]:
            self.safe_path(path)
        for key in ("scope_include", "scope_exclude"):
            if key in p:
                self._strings(p[key], key)
                if any(PurePosixPath(v).is_absolute() or ".." in PurePosixPath(v).parts for v in p[key]):
                    raise LintError("Scope patterns must stay inside the workspace")
        domains = p.get("version_domains")
        if not isinstance(domains, list) or not domains:
            raise LintError("Profile needs version_domains")
        names = set()
        for d in domains:
            if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"] or d["name"] in names:
                raise LintError("Version domains need unique names")
            names.add(d["name"])
            if d.get("kind") not in ("family", "ip"):
                raise LintError("Version domain kind must be family or ip")
            self._strings(d.get("selected_versions"), d["name"] + ".selected_versions")
            self._pattern(d.get("path_pattern"), d["name"] + ".path_pattern", capture=True)
            if d["kind"] == "family":
                self._pattern(d.get("include_pattern"), d["name"] + ".include_pattern", capture=True)
            if bool(d.get("implementation_pattern")) != bool(d.get("register_pattern")):
                raise LintError("Implementation and register patterns must be provided together")
            for key in ("implementation_pattern", "register_pattern"):
                if key in d:
                    self._pattern(d[key], d["name"] + "." + key, capture=True)
            if "camera_families" in d:
                self._strings(d["camera_families"], d["name"] + ".camera_families")
            for key in ("register_compatibility", "include_compatibility"):
                entries = d.get(key, {})
                if not isinstance(entries, dict):
                    raise LintError(f"{key} must map versions to documented compatibility")
                for entry in entries.values():
                    if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip()
                                                             for k in ("reason", "evidence")):
                        raise LintError("Version compatibility requires reason and evidence")
                    self._strings(entry.get("versions"), key + ".versions")
        families = [d for d in domains if d["kind"] == "family"]
        if component == "gdc":
            if families or any("camera_families" in d for d in domains):
                raise LintError("GDC selects its own IP versions; it must not select a Pablo family")
        elif len(families) != 1:
            raise LintError("Pablo IS profile needs exactly one family domain; IP domains are independent")
        self.family_domain = families[0] if families else None
        for selector in p.get("common_family_selectors", []):
            if not isinstance(selector, dict) or not all(isinstance(selector.get(k), str) and selector[k].strip()
                                                        for k in ("path", "reason", "evidence")):
                raise LintError("Common family selector requires path, reason, and evidence")
            self.safe_path(p["camera_root"] + "/" + selector["path"])
        fragments = p.get("fragments")
        if not isinstance(fragments, list) or not fragments:
            raise LintError("Profile needs fragment contracts")
        for fragment in fragments:
            self.safe_path(fragment["path"])
            requirements = fragment.get("requirements")
            if not isinstance(requirements, dict) or not requirements or any(
                not re.fullmatch(r"CONFIG_\w+", k) or v not in ("y", "m", "n") for k, v in requirements.items()
            ):
                raise LintError("Fragment requirements need CONFIG_* keys and y/m/n values")
            for group in fragment.get("exclusive_groups", []):
                self._pattern(group.get("pattern"), "exclusive_groups.pattern")
                self._strings(group.get("selected"), "exclusive_groups.selected")
                if any(not re.fullmatch(group["pattern"], s) or requirements.get(s) not in ("y", "m")
                       for s in group["selected"]):
                    raise LintError("Exclusive selections must match their pattern and enabled requirements")
        contracts = p.get("build_contracts")
        if not isinstance(contracts, dict):
            raise LintError("Profile needs build_contracts")
        unknown = set(contracts) - {"kconfig", "kbuild", "ddk", "selectors", "literals"}
        if unknown:
            raise LintError(f"Unknown build contract kinds: {sorted(unknown)}")
        for kind in ("kconfig", "kbuild", "ddk", "selectors", "literals"):
            if kind == "literals" and kind not in contracts:
                continue
            if not isinstance(contracts.get(kind), list) or (not contracts[kind] and not (
                component == "gdc" and kind in ("selectors", "literals")
            )):
                raise LintError(f"Profile needs build_contracts.{kind}")
            for contract in contracts[kind]:
                self.safe_path(contract["path"])
                fields = {"kconfig": ("symbol",), "kbuild": ("target", "config"),
                          "ddk": ("module", "config"), "selectors": ("name",), "literals": ("name", "keyword")}[kind]
                if any(not isinstance(contract.get(k), str) or not contract[k].strip() for k in fields):
                    raise LintError(f"Missing {kind} contract fields")
                if kind in ("kbuild", "ddk"):
                    self._strings(contract.get("sources"), kind + ".sources")
                if kind == "selectors":
                    self._strings(contract.get("boards"), "selectors.boards")
                if kind == "literals":
                    self._strings(contract.get("values"), "literals.values")
                    if "selector" in contract and (not isinstance(contract["selector"], str) or not contract["selector"]):
                        raise LintError("Literal selector must be a nonempty string")
                for key in ("depends_on", "keyword"):
                    if key in contract and (not isinstance(contract[key], str) or not contract[key].strip()):
                        raise LintError(f"Contract {key} must be a nonempty string")
                if "versioned_source_pattern" in contract:
                    self._pattern(contract["versioned_source_pattern"], "versioned_source_pattern")
                if "versioned_value_pattern" in contract:
                    self._pattern(contract["versioned_value_pattern"], "versioned_value_pattern")
                if "exact" in contract and not isinstance(contract["exact"], bool):
                    raise LintError("Kbuild exact must be boolean")
        self._strings(p.get("protected_sources"), "protected_sources")
        self._strings(p.get("review_variants"), "review_variants")
        self._strings(p.get("evidence"), "evidence")
        required_paths = [f["path"] for f in fragments] + p["protected_sources"] + [
            c["path"] for contracts in contracts.values() for c in contracts
        ]
        for path in required_paths:
            self.safe_path(path)
            if not any(path == s or path.startswith(s.rstrip("/") + "/") for s in p["scope"]):
                raise LintError(f"Profile contract is outside its scope: {path}")
            if any(fnmatch.fnmatchcase(path, pat) for pat in p.get("scope_exclude", [])) or (
                p.get("scope_include") and not any(fnmatch.fnmatchcase(path, pat) for pat in p["scope_include"])
            ):
                raise LintError(f"Profile contract is excluded from its component: {path}")
        self._validate_version_mappings()

    def _validate_version_mappings(self) -> None:
        # Selections and build expectations must agree inside the profile;
        # changing only a label must not make the old implementation pass.
        paths = list(self.profile["protected_sources"])
        for kind in ("kbuild", "ddk", "literals"):
            for contract in self.profile["build_contracts"].get(kind, []):
                for value in contract.get("sources", contract.get("values", [])):
                    if kind == "kbuild" and value.endswith(".o"):
                        value = value[:-2] + ".c"
                    paths.append((PurePosixPath(contract["path"]).parent / value).as_posix())
        prefix = self.profile["camera_root"].rstrip("/") + "/"
        mapped = {d["name"]: set() for d in self.profile["version_domains"]}
        for path in paths:
            local = path[len(prefix):] if path.startswith(prefix) else ""
            owner = self.version_domain(local)
            if not owner:
                continue
            domain, version = owner
            if not self.version_selected(domain, version, local):
                raise LintError(f"Profile {domain['name']} selection conflicts with mapped source: {path}")
            if version in domain["selected_versions"]:
                mapped[domain["name"]].add(version)
        for domain in self.profile["version_domains"]:
            missing = set(domain["selected_versions"]) - mapped[domain["name"]]
            if missing:
                raise LintError(f"Selected {domain['name']} versions have no source/build mapping: {sorted(missing)}")

    def version_domain(self, local: str) -> tuple[dict, str] | None:
        for domain in self.profile["version_domains"]:
            match = re.search(domain["path_pattern"], local)
            if match:
                return domain, match.group(1)
        return None

    def version_selected(self, domain: dict, version: str, local: str) -> bool:
        if version in domain["selected_versions"]:
            return True
        if domain.get("register_pattern") and re.search(domain["register_pattern"], local):
            return any(version in domain.get("register_compatibility", {}).get(v, {}).get("versions", [v])
                       for v in domain["selected_versions"])
        return False

    def protected_paths(self) -> set[str]:
        return set(self.profile["protected_sources"]) | {f["path"] for f in self.profile["fragments"]} | {
            c["path"] for contracts in self.profile["build_contracts"].values() for c in contracts
        }

    def describe(self) -> str:
        versions = ", ".join(f"{d['name']}={','.join(d['selected_versions'])}" for d in self.profile["version_domains"])
        owner = f"{self.component} / " if self.component != "combined" else ""
        return f"{owner}profile {self.profile['id']}: {self.profile['device']} / {self.profile['soc']}; {versions}"

    def _validate_suppressions(self) -> None:
        for item in self.data.get("suppressions", []):
            if not isinstance(item, dict) or not all(
                isinstance(item.get(k), str) and item[k].strip()
                for k in ("rule", "path", "reason", "owner")
            ):
                raise LintError("Suppression needs rule, path, reason, and owner")
            if item["rule"] not in self.rules and not re.fullmatch(r"CHECKPATCH\.[A-Z0-9_]+", item["rule"]):
                raise LintError(f"Unknown suppression rule: {item['rule']}")
            if "evidence" in item and (not isinstance(item["evidence"], str) or not item["evidence"].strip()):
                raise LintError("Suppression evidence must be a nonempty substring")

    def safe_path(self, value: str | Path) -> Path:
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise LintError(f"Path is outside workspace: {value}")
        return path

    def relative(self, value: str | Path) -> str:
        return self.safe_path(value).relative_to(self.root).as_posix()

    def in_scope(self, rel: str) -> bool:
        p = Path(rel)
        allowed = any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in self.data["scope"])
        if self.data.get("scope_include"):
            allowed = allowed and any(fnmatch.fnmatchcase(rel, pattern) for pattern in self.data["scope_include"])
        return allowed and (p.suffix in self.data["extensions"] or p.name in self.data["filenames"]) and not any(
            fnmatch.fnmatchcase(rel, pattern) for pattern in self.data["exclude"]
        )

    def files(self, allow_missing: bool = False) -> list[str]:
        found = set()
        for scope in self.data["scope"]:
            path = self.safe_path(scope)
            if not path.exists():
                if allow_missing:
                    continue
                raise LintError(f"Configured scope is missing: {scope}")
            if path.is_file():
                candidates = [path]
            else:
                candidates = path.rglob("*")
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                # Symlinks must not escape the workspace or alias a source path.
                rel = candidate.relative_to(self.root).as_posix()
                if self.in_scope(rel):
                    if candidate.is_symlink() or candidate.resolve() != candidate.absolute():
                        raise LintError(f"Source symlink is not supported: {rel}")
                    found.add(rel)
        return sorted(found)

    def camera_relative(self, rel: str) -> str:
        prefix = self.data["camera_root"].rstrip("/") + "/"
        return rel[len(prefix):] if rel.startswith(prefix) else ""

    def layer(self, rel: str) -> str:
        local = self.camera_relative(rel)
        if not local:
            return "build"
        for layer in self.data["layer_patterns"]:
            if any(fnmatch.fnmatchcase(local, pattern) for pattern in layer["patterns"]):
                return layer["name"]
        return "core"

    def diagnostic(self, rule: str, path: str, line: int, message: str, evidence: str = "", hint: str = "") -> Diagnostic:
        definition = self.rules[rule]
        severity = self.data.get("severity_overrides", {}).get(rule, definition["severity"])
        return Diagnostic(path, max(1, line), rule, severity, message, hint or definition["fix"], evidence.strip()[:500])
