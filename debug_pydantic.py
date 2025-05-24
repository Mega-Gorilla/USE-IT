import inspect

from pydantic import BaseModel

from browser_use.controller.views import ClickElementAction


# Check the pydantic detection logic
def click_element_by_index(params: ClickElementAction, browser_session):
	pass


sig = inspect.signature(click_element_by_index)
parameters = list(sig.parameters.values())
parameter_names = [param.name for param in parameters]

print('Parameters:', parameter_names)
print('First param name:', parameters[0].name)
print('First param annotation:', parameters[0].annotation)
print('Is BaseModel:', issubclass(parameters[0].annotation, BaseModel))

# Check the name detection logic
name_check = parameters[0].name in ['params', 'param', 'model'] or parameters[0].name.endswith('_model')
print('Name check passed:', name_check)

is_pydantic = (
	parameters
	and len(parameters) > 0
	and hasattr(parameters[0], 'annotation')
	and parameters[0].annotation != parameters[0].empty
	and issubclass(parameters[0].annotation, BaseModel)
	and name_check
)
print('Is pydantic:', is_pydantic)
