# Refactor: Pydantic Removal from Domain Layer

**Status:** Completed
**Commit:** `7726ba8` - *refactor(phase1): remove Pydantic from Domain layer*
**Date:** Pre-v0.1.0

## Summary

All domain entities were converted from Pydantic `BaseModel` subclasses to plain
Python `@dataclass` classes. This was Phase 1 of the Clean Architecture migration.

## Motivation

The Domain layer must be framework-free per Clean Architecture. Pydantic is an
external framework, and using it in the innermost layer meant:

- Domain entities carried runtime validation overhead not needed internally
- Tests required Pydantic to be installed even for pure business logic
- Serialization behavior (`model_dump()`, `.json()`) leaked framework concerns
- Field validation rules mixed domain invariants with I/O validation

## What Changed

### Entities Converted

| Entity | Before | After |
|---|---|---|
| `TorznabQuery` | `BaseModel` | `@dataclass` |
| `TorznabItem` | `BaseModel` | `@dataclass` |
| `TorznabCaps` | `BaseModel` | `@dataclass` |
| `SearchResult` | `BaseModel` | `@dataclass` |
| `CrawlJob` | `BaseModel` | `@dataclass` |
| `StageResult` | `BaseModel` | `@dataclass` |
| `YamlPluginDefinition` | `BaseModel` | `@dataclass` |

### Pattern Changes

**Default values with mutable types:**
```python
# Before (Pydantic handles this automatically)
class TorznabQuery(BaseModel):
    cat: list[int] = []

# After (explicit factory to avoid mutable default gotcha)
@dataclass
class TorznabQuery:
    cat: list[int] = field(default_factory=list)
```

**Immutable value objects:**
```python
# Before
class TorznabCaps(BaseModel):
    class Config:
        frozen = True

# After
@dataclass(frozen=True)
class TorznabCaps:
    ...
```

**Validation moved to factories:**
```python
# Before: validation in entity
class TorznabQuery(BaseModel):
    @validator("t")
    def validate_type(cls, v):
        if v not in ("search", "caps"):
            raise ValueError(...)

# After: validation in application-layer factory
def create_torznab_query(t: str, **kwargs) -> TorznabQuery:
    if t not in ("search", "caps"):
        raise TorznabError(...)
    return TorznabQuery(t=t, **kwargs)
```

## Where Pydantic Remains

Pydantic was only removed from the Domain layer. It is still used in:

- **Infrastructure/Config:** `pydantic-settings` for configuration loading and validation
- **Interfaces/HTTP:** FastAPI request/response models (where Pydantic is appropriate)

This is correct per Clean Architecture: frameworks belong in the outer layers.

## Impact

- Domain layer has zero external dependencies (only stdlib + `typing`)
- Domain tests run without any framework imports
- Entity construction is faster (no Pydantic validation overhead)
- Type hints remain identical (modern Python 3.10+ syntax)
