"""Caster: the thin layer over git + Hydra + DVC + Croissant + GTO + RO-Crate/BagIt.

Caster's own code is justified only where it encodes knowledge no library has:
the link from a run to its commit, the shape of the project's provenance, and
the dataset-disposition policy. Everywhere a mature library performs the
mechanism, Caster calls it.
"""

__version__ = "0.1.0"
