"""Optional additive extensions layered on top of the official UEFormat reader.

The extension package is deliberately separate from ``deserialize`` and
``create``.  Standard UEFormat files must remain readable when this package is
absent, disabled, or unable to decode an extension payload.
"""

