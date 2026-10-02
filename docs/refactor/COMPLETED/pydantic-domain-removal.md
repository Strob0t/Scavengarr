[← Back to Index](../../features/README.md)

# Refactor: Pydantic Removal from Domain Layer

**Status:** Completed
**Commit:** `7726ba8` — *refactor(phase1): remove Pydantic from Domain layer*
**Date:** Pre-v0.1.0

## Summary

The remaining Pydantic models in the Domain layer — `StageResult` and the YAML plugin schema — were converted to plain Python `@dataclass` classes. The Torznab entities and `CrawlJob` were already dataclasses before this commit. This was Phase 1 of the Clean Architecture migration.

## Motivation

The Domain layer must be framework-free per Clean Architecture. Pydantic is an external framework, and using it in the innermost layer meant:

- Domain entities carried runtime validation overhead not needed internally
- Tests required Pydantic to be installed even for pure business logic
- Serialization behavior (`model_dump()`, `.json()`) leaked framework concerns
- Field validation rules mixed domain invariants with I/O validation

## What Changed

### Models Converted

| Model | Before | After |
|---|---|---|
| `StageResult` (`domain/plugins/base.py`) | `BaseModel` | `@dataclass` (later removed in `03454a8`) |
| `HttpOverrides`, `AuthConfig` | `BaseModel` in `domain/plugins/schema.py` | `@dataclass(frozen=True)` in `domain/plugins/plugin_schema.py` (still present) |
| `PaginationConfig`, `NestedSelector`, `StageSelectors`, `ScrapingStage`, `ScrapySelectors`, `PlaywrightLocators`, `ScrapingConfig`, `YamlPluginDefinition` | `BaseModel` in `domain/plugins/schema.py` | `@dataclass(frozen=True)` in `domain/plugins/plugin_schema.py` (later removed with the YAML plugin system in `42fced9`) |

Already dataclasses before `7726ba8` (unchanged): `TorznabQuery`, `TorznabItem`, `TorznabCaps` (`domain/entities/torznab.py`, `frozen=True`), `CrawlJob` (`domain/entities/crawljob.py`), `SearchResult` (`domain/plugins/base.py`).

`domain/plugins/schema.py` was deleted. The Pydantic validation moved to `infrastructure/plugins/validation_schema.py` (later removed in `b72c420`), and `infrastructure/plugins/adapters.py` converted validated Pydantic models into the domain dataclasses (later removed in `352f7ca`).

### Pattern Changes

**Default values with mutable types** (`SearchResult.metadata`):

```python
# Before (shared mutable/None default)
@dataclass
class SearchResult:
    metadata: Dict[str, Any] = None

# After (explicit factory to avoid mutable default gotcha)
@dataclass
class SearchResult:
    metadata: dict[str, Any] = field(default_factory=dict)
```

**Immutable value objects:**

```python
# Before
class AuthConfig(BaseModel):
    ...

# After
@dataclass(frozen=True)
class AuthConfig:
    ...
```

**Validation moved to infrastructure:**

```python
# Before: validators on the domain model (domain/plugins/schema.py)
class YamlPluginDefinition(BaseModel):
    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return v.strip()

# After: Pydantic validation in infrastructure/plugins/validation_schema.py,
# then conversion to the pure domain dataclass via infrastructure/plugins/adapters.py
```

Torznab request validation never lived in an entity: it is done in `TorznabSearchUseCase.execute()` (raises `TorznabBadRequest`) and in the Torznab router (raises `TorznabUnsupportedAction` for actions other than `caps`/`search`).

## Where Pydantic Remains

Pydantic was only removed from the Domain layer. Today it is used only in:

- **Infrastructure/Config:** `pydantic-settings` for configuration loading and validation (`src/scavengarr/infrastructure/config/schema.py`)

This is correct per Clean Architecture: frameworks belong in the outer layers.

## Impact

- Domain layer has zero external dependencies (only stdlib + `typing`)
- Domain tests run without any framework imports
- Entity construction is faster (no Pydantic validation overhead)
- Type hints remain identical (modern Python 3.10+ syntax)
