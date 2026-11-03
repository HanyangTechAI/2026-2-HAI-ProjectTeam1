from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectorAblationConfig:
    name: str = "full"
    
    constraint_preservation: bool = True
    
    temporal_resolution: bool = True
    
    mandatory_state: bool = True
    
    token_aware_packing: bool = True
    
    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("ablation name must be a non-empty string")
        
        for field_name in ("constraint_preservation", "temporal_resolution", "mandatory_state", "token_aware_packing"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be bool")
            
        if not self.temporal_resolution and self.mandatory_state:
            raise ValueError("mandatory_state requires temporal_resolution")
        
    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "constraint_preservation": self.constraint_preservation,
            "temporal_resolution": self.temporal_resolution,
            "mandatory_state": self.mandatory_state,
            "token_aware_packing": self.token_aware_packing,
        }
        
        
FULL_ABLATION = SelectorAblationConfig()


ABLATION_PRESETS: dict[str, SelectorAblationConfig] = {
    "full": FULL_ABLATION,
    "no_constraint_preservation": SelectorAblationConfig(
        name="no_constraint_preservation",
        constraint_preservation=False
    ),
    "no_temporal": SelectorAblationConfig(
        name="no_temporal",
        temporal_resolution=False,
        mandatory_state=False,
    ),
    "no_state_mandatory": SelectorAblationConfig(
        name="no_state_mandatory",
        mandatory_state=False,
    ),
    "no_budget_aware_packing": SelectorAblationConfig(
        name="no_budget_aware_packing",
        token_aware_packing=False,
    ),
}


def get_ablation_config(name: str) -> SelectorAblationConfig:
    try:
        return ABLATION_PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"unknown ablation preset: {name}") from exc