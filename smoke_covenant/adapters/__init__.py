"""Renderer adapters. Each one routes a host renderer's asset resolution through
the hermetic gate. Adapters are thin by design: the rule lives in `gate.py`, and
an adapter only knows where its renderer opens things."""
