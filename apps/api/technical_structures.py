"""Existing API import forwards to the shared structure adapter."""
import sys
from custom_model.application import technical_structure_adapter as _implementation
sys.modules[__name__] = _implementation
