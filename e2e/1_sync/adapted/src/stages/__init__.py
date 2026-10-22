"""The science stages. LAURA OWNS THIS PACKAGE.

One file per stage, zero flowgentic imports in any of them. The wiring that
calls them in order lives one level up, in `../pipeline.py` -- not in here,
since the stage order is the seam between this package and main.py, not a
science concern itself.
"""
