"""Pure/defensive API helpers, grouped by investigation-flow concern.

No DB sessions and no FastAPI app state in here — these are the testable
building blocks behind the api.routes.* surface. See
docs/BACKEND_MODULARIZATION_PLAN.md (helpers/ split of api/flow_support.py).
"""
