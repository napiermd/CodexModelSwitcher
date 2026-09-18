"""Synthetic Azure server for the isolated, ephemeral pilot network."""
from pilot import MockAzure

with MockAzure(('0.0.0.0', 8081)) as server:
    server.serve_forever()
