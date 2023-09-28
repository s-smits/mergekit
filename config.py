from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel
from typing_extensions import TypeAlias

from common import ModelReference

ScalarOrGradient: TypeAlias = Union[float, List[float]]


class ConditionalParameter(BaseModel):
    value: ScalarOrGradient
    filter: Optional[str] = None


ParameterSetting: TypeAlias = Union[
    ConditionalParameter, List[ConditionalParameter], ScalarOrGradient
]


def evaluate_setting(
    tensor_name: str, setting: ParameterSetting, t: float = 0
) -> float:
    if isinstance(setting, (float, int, bool, str)):
        return setting
    elif isinstance(setting, list):
        if all(isinstance(e, (int, float)) for e in setting):
            scaled = t * (len(setting) - 1)
            i0 = int(scaled)
            i1 = min(len(setting) - 1, i0 + 1)
            frac = scaled - i0

            return (1 - frac) * setting[i0] + frac * setting[i1]
        elif all(isinstance(e, (float, int, bool, str)) for e in setting):
            return setting[int(t * (len(setting) - 1))]
        else:
            for cond in setting:
                if (
                    (cond.filter is None)
                    or (cond.filter == "*")
                    or cond.filter in tensor_name
                ):
                    res = evaluate_setting(tensor_name, cond.value, t)
                    return res
    return None


class InputSliceDefinition(BaseModel):
    model: str
    layer_range: Tuple[int, int]
    parameters: Optional[Dict[str, ParameterSetting]] = None


class InputModelDefinition(BaseModel):
    model: str
    parameters: Optional[Dict[str, ParameterSetting]] = None


class OutputSliceDefinition(BaseModel):
    sources: List[InputSliceDefinition]
    base_model: Optional[str] = None
    residual_weight: Optional[float] = None
    parameters: Optional[Dict[str, ParameterSetting]] = None


class MergeConfiguration(BaseModel):
    merge_method: str
    slices: Optional[List[OutputSliceDefinition]] = None
    models: Optional[List[InputModelDefinition]] = None
    model_parameters: Dict[str, Dict[str, ParameterSetting]] = None
    parameters: Optional[Dict[str, ParameterSetting]] = None
    base_model: Optional[str] = None
    dtype: Optional[str] = None

    def referenced_models(self) -> List[ModelReference]:
        models = set()
        if self.model_parameters:
            for key in self.model_parameters:
                models.add(ModelReference.parse(key))
        for s in self.slices:
            for src in s.sources:
                models.add(ModelReference.parse(src.model))
        return list(models)

    def validate(self):
        if ((not self.slices) and (not self.models)) or (self.slices and self.models):
            raise RuntimeError("Must specify either output slices or models to merge")


class ConfigReader(BaseModel):
    config: MergeConfiguration
    tensor_name: str
    t: float
    slice_out: Optional[OutputSliceDefinition]
    slices_in: Optional[List[InputSliceDefinition]]

    @property
    def base_model(self) -> Optional[ModelReference]:
        if self.slice_out and self.slice_out.base_model:
            res = self.slice_out.base_model
        else:
            res = self.config.base_model

        if res:
            return ModelReference.parse(res)
        return None

    def parameter(
        self, name: str, model: Optional[ModelReference] = None, default: Any = None, required: bool = False,
    ) -> Any:
        if model and self.slices_in:
            for s in self.slices_in:
                if s.model == str(model) and s.parameters and name in s.parameters:
                    return evaluate_setting(
                        self.tensor_name, s.parameters[name], self.t
                    )

        if self.slice_out:
            if self.slice_out.parameters and name in self.slice_out.parameters:
                return evaluate_setting(
                    self.tensor_name, self.slice_out.parameters[name], self.t
                )

        if (
            self.config.model_parameters
            and model
            and str(model) in self.config.model_parameters
        ):
            if name in self.config.model_parameters[self.slice_in.model]:
                return evaluate_setting(
                    self.tensor_name,
                    self.config.model_parameters[str(model)][name],
                    self.t,
                )

        if self.config.parameters and name in self.config.parameters:
            return evaluate_setting(
                self.tensor_name,
                self.config.parameters[name],
                self.t,
            )

        if required:
            suffix = f" for {str(model)}.{self.tensor_name}" if model else ""
            raise RuntimeError(f"Missing required parameter {name}{suffix}")
        return default
