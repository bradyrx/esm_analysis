# flake8: noqa
from pkg_resources import DistributionNotFound, get_distribution

from . import (
    carbon,
    composite,
    conversions,
    grid,
    physics,
    spatial,
    stats,
    temporal,
    testing,
)
from .accessor import GridAccessor
from .versioning.print_versions import show_versions

try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:  # pragma: no cover
    # package is not installed
    pass  # pragma: no cover
