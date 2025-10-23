# compat shim: re-esporta tutto da schemas
from .schemas import *
__all__ = [name for name in globals().keys() if not name.startswith("_")]

# Placeholder temporaneo per compatibilità
class Offramp:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
