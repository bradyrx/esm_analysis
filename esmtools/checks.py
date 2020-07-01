from functools import wraps

import xarray as xr
from pandas.core.indexes.datetimes import DatetimeIndex


# https://stackoverflow.com/questions/10610824/
# python-shortcut-for-writing-decorators-which-accept-arguments
def dec_args_kwargs(wrapper):
    return lambda *dec_args, **dec_kwargs: lambda func: wrapper(
        func, *dec_args, **dec_kwargs
    )


def is_time_index(xobj, kind):
    """
    Checks that xobj coming through is a DatetimeIndex or CFTimeIndex.

    This checks that `esmtools` is converting the DataArray to an index,
    i.e. through .to_index()
    """
    if not (isinstance(xobj, xr.CFTimeIndex) or isinstance(xobj, DatetimeIndex)):
        raise ValueError(
            f'Your {kind} object must be either an xr.CFTimeIndex or '
            f'pd.DatetimeIndex.'
        )
    return True


@dec_args_kwargs
# Lifted from climpred. This was originally written by Andrew Huang.
def is_xarray(func, *dec_args):
    """
    Decorate a function to ensure the first arg being submitted is
    either a Dataset or DataArray.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            ds_da_locs = dec_args[0]
            if not isinstance(ds_da_locs, list):
                ds_da_locs = [ds_da_locs]

            for loc in ds_da_locs:
                if isinstance(loc, int):
                    ds_da = args[loc]
                elif isinstance(loc, str):
                    ds_da = kwargs[loc]

                is_ds_da = isinstance(ds_da, (xr.Dataset, xr.DataArray))
                if not is_ds_da:
                    typecheck = type(ds_da)
                    raise IOError(
                        f"""The input data is not an xarray DataArray or
                        Dataset.

                        Your input was of type: {typecheck}"""
                    )
        except IndexError:
            pass
        # this is outside of the try/except so that the traceback is relevant
        # to the actual function call rather than showing a simple Exception
        # (probably IndexError from trying to subselect an empty dec_args list)
        return func(*args, **kwargs)

    return wrapper
