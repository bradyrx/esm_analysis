import warnings

import climpred.stats as st
import numpy as np
import numpy.polynomial.polynomial as poly
import scipy
import xarray as xr

from .checks import has_missing, is_xarray
from .timeutils import TimeUtilAccessor
from .utils import get_dims, match_nans


@is_xarray(0)
def standardize(ds, dim="time"):
    """Standardize Dataset/DataArray

    .. math::
        \\frac{x - \\mu_{x}}{\\sigma_{x}}

    Args:
        ds (xarray object): Dataset or DataArray with variable(s) to standardize.
        dim (optional str): Which dimension to standardize over (default 'time').

    Returns:
        stdized (xarray object): Standardized variable(s).
    """
    stdized = (ds - ds.mean(dim)) / ds.std(dim)
    return stdized


@is_xarray(0)
def nanmean(ds, dim="time"):
    """Compute mean NaNs and suppress warning from numpy"""
    if "time" in ds.dims:
        mask = ds.isnull().isel(time=0)
    else:
        mask = ds.isnull()
    ds = ds.fillna(0).mean(dim)
    ds = ds.where(~mask)
    return ds


@is_xarray(0)
def cos_weight(da, lat_coord="lat", lon_coord="lon", one_dimensional=True):
    """
    Area-weights data on a regular (e.g. 360x180) grid that does not come with
    cell areas. Uses cosine-weighting.

    Parameters
    ----------
    da : DataArray with longitude and latitude
    lat_coord : str (optional)
        Name of latitude coordinate
    lon_coord : str (optional)
        Name of longitude coordinate
    one_dimensional : bool (optional)
        If true, assumes that lat and lon are 1D (i.e. not a meshgrid)

    Returns
    -------
    aw_da : Area-weighted DataArray

    Examples
    --------
    import esmtools as et
    da_aw = et.stats.reg_aw(SST)
    """
    non_spatial = [i for i in get_dims(da) if i not in [lat_coord, lon_coord]]
    filter_dict = {}
    while len(non_spatial) > 0:
        filter_dict.update({non_spatial[0]: 0})
        non_spatial.pop(0)
    if one_dimensional:
        lon, lat = np.meshgrid(da[lon_coord], da[lat_coord])
    else:
        lat = da[lat_coord]
    # NaN out land to not go into area-weighting
    lat = lat.astype("float")
    nan_mask = np.asarray(da.isel(filter_dict).isnull())
    lat[nan_mask] = np.nan
    cos_lat = np.cos(np.deg2rad(lat))
    aw_da = (da * cos_lat).sum(lat_coord).sum(lon_coord) / np.nansum(cos_lat)
    return aw_da


@is_xarray(0)
def area_weight(da, area_coord="area"):
    """
    Returns an area-weighted time series from the input xarray dataarray. This
    automatically figures out spatial dimensions vs. other dimensions. I.e.,
    this function works for just a single realization or for many realizations.
    See `reg_aw` if you have a regular (e.g. 360x180) grid that does not
    contain cell areas.

    It also looks like xarray is implementing a feature like this.

    NOTE: This currently does not support datasets (of multiple variables)
    The user can alleviate this by using the .apply() function.

    Parameters
    ----------
    da : DataArray
    area_coord : str (defaults to 'area')
        Name of area coordinate if different from 'area'

    Returns
    -------
    aw_da : Area-weighted DataArray
    """
    area = da[area_coord]
    # Mask the area coordinate in case you've got a bunch of NaNs, e.g. a mask
    # or land.
    dimlist = get_dims(da)
    # Pull out coordinates that aren't spatial. Time, ensemble members, etc.
    non_spatial = [i for i in dimlist if i not in get_dims(area)]
    filter_dict = {}
    while len(non_spatial) > 0:
        filter_dict.update({non_spatial[0]: 0})
        non_spatial.pop(0)
    masked_area = area.where(da.isel(filter_dict).notnull())
    # Compute area-weighting.
    dimlist = get_dims(masked_area)
    aw_da = da * masked_area
    # Sum over arbitrary number of dimensions.
    while len(dimlist) > 0:
        print(f"Summing over {dimlist[0]}")
        aw_da = aw_da.sum(dimlist[0])
        dimlist.pop(0)
    # Finish area-weighting by dividing by sum of area coordinate.
    aw_da = aw_da / masked_area.sum()
    return aw_da


def _check_y_not_independent_variable(y, dim):
    """Checks that `y` is not the independent variable in statistics functions.

    Args:
        y (xr.DataArray or xr.Dataset): Dependent variable from statistics functions.
        dim (str): Dimension statistical function is being applied over.

    Raises:
        ValueError: If `y` is a DataArray and equal to `dim`. This infers that something
            like a time axis is being placed in the dependent variable.
    """
    if isinstance(y, xr.DataArray) and (y.name == dim):
        raise ValueError(
            f"Dependent variable y should not be the same as the dim {dim} being "
            "applied over. Change your y variable to x."
        )


def _convert_time_and_return_slope_factor(x, dim):
    """Converts `x` to numeric time (if datetime) and returns slope factor.

    This accounts for differences in length of months, leap years, etc. when fitting
    a regression and also ensures that the numpy functions don't break with datetimes.

    Args:
        x (xr.DataArray or xr.Dataset): Independent variable from statistical functions.
        dim (str): Dimension statistical function is being applied over.

    Returns:
        x (xr.DataArray or xr.Dataset): If `x` is a time axis, converts to numeric
            time.
        slope_factor (float): Factor to multiply slope by if returning regression
            results. This accounts for the fact that datetimes are converted to
            "days since 1990-01-01" numeric time and thus the answer comes out
            in the original units per day (e.g., degC/day). This auto-converts to
            the original temporal frequency (e.g., degC/year) if the calendar
            can be inferred.
    """
    slope_factor = 1.0
    if isinstance(x, xr.DataArray):
        x_type = x.timeutils.type
        if x_type in ["DatetimeIndex", "CFTimeIndex"]:
            slope_factor = x.timeutils.slope_factor
            x = x.timeutils.return_numeric_time()
    return x, slope_factor


def _handle_nans(x, y, nan_policy):
    """Modifies `x` and `y` based on `nan_policy`.

    Args:
        x, y (xr.DataArray or ndarrays): Two time series to which statistical function
            is being applied.
        nan_policy (str): One of ['none', 'propagate', 'raise', 'omit', 'drop']. If
            'none' or 'propagate', return unmodified so the nans can be propagated
            through in the functions. If 'raise', raises a warning if there are any
            nans in `x` or `y`. If 'omit' or 'drop', removes values that contain
            a nan in either `x` or `y` and returns resulting `x` and `y`.

    Returns:
        x, y (xr.DataArray or ndarrays): Modified `x` and `y` datasets.

    Raises:
        ValueError: If `nan_policy` is 'raise' and there are nans in either `x` or `y`,
            if `nan_policy` is no one of ['none', 'propagate', 'raise', 'omit',
            'drop'], or if `x` or `y` are larger than 1-dimensional.
    """
    # Only support 1D, since we are doing `~np.isnan()` indexing for 'omit'/'drop'.
    if (x.ndim > 1) or (y.ndim > 1):
        raise ValueError(
            f"x and y must be 1-dimensional. Got {x.ndim} for x and {y.ndim} for y."
        )

    if nan_policy in ["none", "propagate"]:
        return x, y
    elif nan_policy == "raise":
        if has_missing(x) or has_missing(y):
            raise ValueError(
                "Input data contains NaNs. Consider changing `nan_policy` to 'none' "
                "or 'omit'. Or get rid of those NaNs somehow."
            )
        else:
            return x, y
    elif nan_policy in ["omit", "drop"]:
        if has_missing(x) or has_missing(y):
            x_mod, y_mod = match_nans(x, y)
            x_mod = x_mod[np.isfinite(x_mod)]
            y_mod = y_mod[np.isfinite(y_mod)]
            return x_mod, y_mod
        else:
            return x, y
    else:
        raise ValueError(
            f"{nan_policy} not one of ['none', 'propagate', 'raise', 'omit', 'drop']"
        )


def _polyfit(x, y, order, nan_policy):
    """Helper function for performing ``np.poly.polyfit`` which is used in both
    ``polyfit`` and ``rm_poly``.

    Args:
        x, y (ndarrays): Independent and dependent variables used in the polynomial
            fit.
        order (int): Order of polynomial fit to perform.
        nan_policy (str, optional): Policy to use when handling nans. Defaults to
            "none".

            * 'none', 'propagate': If a NaN exists anywhere on the given dimension,
                return nans for that whole dimension.
            * 'raise': If a NaN exists at all in the datasets, raise an error.
            * 'omit', 'drop': If a NaN exists in `x` or `y`, drop that index and
                compute the slope without it.

    Returns:
        fit (ndarray): If ``nan_policy`` is 'none' or 'propagate' and a nan exists
            in the time series, returns all nans. Otherwise, returns the polynomial
            fit.
    """
    x_mod, y_mod = _handle_nans(x, y, nan_policy)
    if (nan_policy in ['omit', 'drop']) and (x_mod.size == 0):
        return np.full(len(x), np.nan)
    elif (nan_policy in ['none', 'propagate']) and (has_missing(x_mod)):
        return np.full(len(x), np.nan)
    else:
        # fit to data without nans, return applied to original independent axis.
        coefs = poly.polyfit(x_mod, y_mod, order)
        return poly.polyval(x, coefs)


def _warn_if_not_converted_to_original_time_units(x):
    """Administers warning if the independent variable is in datetimes and the
    calendar frequency could not be inferred.

    Args:
        x (xr.DataArray or xr.Dataset): Independent variable for statistical functions.
    """
    if isinstance(x, xr.DataArray):
        x_type = x.timeutils.type
        if x_type in ["DatetimeIndex", "CFTimeIndex"]:
            freq = x.timeutils.freq
            if freq is None:
                warnings.warn(
                    "Datetime frequency not detected. Slope and std. errors will be "
                    "in original units per day (e.g., degC per day). Multiply by "
                    "e.g., 365.25 to convert to original units per year."
                )


@is_xarray([0, 1])
def linear_slope(x, y, dim="time", nan_policy="none"):
    """Returns the linear slope with y regressed onto x.

    .. note::

        This function will try to infer the time freqency of sampling if ``x`` is in
        datetime units. The final slope will be returned in the original units per
        that frequency (e.g. SST per year). If the frequency cannot be inferred
        (e.g. because the sampling is irregular), it will return in the original
        units per day (e.g. SST per day).

    Args:
        x (xarray object): Independent variable (predictor) for linear regression.
        y (xarray object): Dependent variable (predictand) for linear regression.
        dim (str, optional): Dimension to apply linear regression over.
            Defaults to "time".
        nan_policy (str, optional): Policy to use when handling nans. Defaults to
            "none".

            * 'none', 'propagate': If a NaN exists anywhere on the given dimension,
                return nans for that whole dimension.
            * 'raise': If a NaN exists at all in the datasets, raise an error.
            * 'omit', 'drop': If a NaN exists in `x` or `y`, drop that index and
                compute the slope without it.

    Returns:
        xarray object: Slopes computed through a least-squares linear regression.
    """
    _check_y_not_independent_variable(y, dim)
    x, slope_factor = _convert_time_and_return_slope_factor(x, dim)

    def _linear_slope(x, y, nan_policy):
        x, y = _handle_nans(x, y, nan_policy)
        if (nan_policy in ['omit', 'drop']) and (x.size == 0):
            return np.asarray([np.nan])
        elif (nan_policy in ['none', 'propagate']) and (has_missing(x)):
            return np.asarray([np.nan])
        else:
            return np.polyfit(x, y, 1)[0]

    slopes = xr.apply_ufunc(
        _linear_slope,
        x,
        y,
        nan_policy,
        vectorize=True,
        dask="parallelized",
        input_core_dims=[[dim], [dim], []],
        output_dtypes=["float64"],
    )
    _warn_if_not_converted_to_original_time_units(x)
    return slopes * slope_factor


@is_xarray([0, 1])
def linregress(x, y, dim="time", nan_policy="none"):
    """Vectorized applciation of ``scipy.stats.linregress``.

    .. note::

        This function will try to infer the time freqency of sampling if ``x`` is in
        datetime units. The final slope and standard error will be returned in the
        original units per that frequency (e.g. SST per year). If the frequency
        cannot be inferred (e.g. because the sampling is irregular), it will return in
        the original units per day (e.g. SST per day).

    Args:
        x (xarray object): Independent variable (predictor) for linear regression.
        y (xarray object): Dependent variable (predictand) for linear regression.
        dim (str, optional): Dimension to apply linear regression over.
            Defaults to "time".
        nan_policy (str, optional): Policy to use when handling nans. Defaults to
            "none".

            * 'none', 'propagate': If a NaN exists anywhere on the given dimension,
                return nans for that whole dimension.
            * 'raise': If a NaN exists at all in the datasets, raise an error.
            * 'omit', 'drop': If a NaN exists in `x` or `y`, drop that index and
                compute the slope without it.

    Returns:
        xarray object: Slope, intercept, correlation, p value, and standard error for
            the linear regression. These 5 parameters are added as a new dimension
            "parameter".

    """
    _check_y_not_independent_variable(y, dim)
    x, slope_factor = _convert_time_and_return_slope_factor(x, dim)

    def _linregress(x, y, slope_factor, nan_policy):
        x, y = _handle_nans(x, y, nan_policy)
        if (nan_policy in ['omit', 'drop']) and (x.size == 0):
            return np.full(5, np.nan)
        else:
            m, b, r, p, e = scipy.stats.linregress(x, y)
            # Multiply slope by factor. If time indices converted to numeric units, this
            # gets them back to the original units.
            m *= slope_factor
            e *= slope_factor
            return np.array([m, b, r, p, e])

    results = xr.apply_ufunc(
        _linregress,
        x,
        y,
        slope_factor,
        nan_policy,
        vectorize=True,
        dask="parallelized",
        input_core_dims=[[dim], [dim], [], []],
        output_core_dims=[["parameter"]],
        output_dtypes=["float64"],
        output_sizes={"parameter": 5},
    )
    results = results.assign_coords(
        parameter=["slope", "intercept", "rvalue", "pvalue", "stderr"]
    )
    _warn_if_not_converted_to_original_time_units(x)
    return results


@is_xarray(0)
def polyfit(x, y, order, dim="time", nan_policy="none"):
    """Returns the fitted polynomial line of ``y`` regressed onto ``x``.

    Args:
        x, y (xr.DataArray or xr.Dataset): Independent and dependent variables used in
            the polynomial fit.
        order (int): Order of polynomial fit to perform.
        dim (str, optional): Dimension to apply polynomial fit over.
            Defaults to "time".
        nan_policy (str, optional): Policy to use when handling nans. Defaults to
            "none".

            * 'none', 'propagate': If a NaN exists anywhere on the given dimension,
                return nans for that whole dimension.
            * 'raise': If a NaN exists at all in the datasets, raise an error.
            * 'omit', 'drop': If a NaN exists in `x` or `y`, drop that index and
                compute the slope without it.

    Returns:
        xarray object: The polynomial fit for ``y`` regressed onto ``x``. Has the same
            dimensions as ``y``.
    """
    _check_y_not_independent_variable(y, dim)
    x, _ = _convert_time_and_return_slope_factor(x, dim)

    return xr.apply_ufunc(
        _polyfit,
        x,
        y,
        order,
        nan_policy,
        vectorize=True,
        dask="parallelized",
        input_core_dims=[[dim], [dim], [], []],
        output_core_dims=[[dim]],
        output_dtypes=["float"],
    )


@is_xarray(0)
def rm_poly(x, y, order, dim="time", nan_policy="none"):
    """Removes a polynomial fit from ``y`` regressed onto ``x``.

    Args:
        x, y (xr.DataArray or xr.Dataset): Independent and dependent variables used in
            the polynomial fit.
        order (int): Order of polynomial fit to perform.
        dim (str, optional): Dimension to apply polynomial fit over.
            Defaults to "time".
        nan_policy (str, optional): Policy to use when handling nans. Defaults to
            "none".

            * 'none', 'propagate': If a NaN exists anywhere on the given dimension,
                return nans for that whole dimension.
            * 'raise': If a NaN exists at all in the datasets, raise an error.
            * 'omit', 'drop': If a NaN exists in `x` or `y`, drop that index and
                compute the slope without it.

    Returns:
        xarray object: ``y`` with polynomial fit of order ``order`` removed.
    """
    _check_y_not_independent_variable(y, dim)
    x, _ = _convert_time_and_return_slope_factor(x, dim)

    def _rm_poly(x, y, order, nan_policy):
        fit = _polyfit(x, y, order, nan_policy)
        return y - fit

    return xr.apply_ufunc(
        _rm_poly,
        x,
        y,
        order,
        nan_policy,
        vectorize=True,
        dask="parallelized",
        input_core_dims=[[dim], [dim], [], []],
        output_core_dims=[[dim]],
        output_dtypes=["float64"],
    )


@is_xarray(0)
def rm_trend(x, y, dim="time", nan_policy="none"):
    """Removes a linear fit from ``y`` regressed onto ``x``.

    Args:
        x, y (xr.DataArray or xr.Dataset): Independent and dependent variables used in
            the linear fit.
        dim (str, optional): Dimension to apply linear fit over.
            Defaults to "time".
        nan_policy (str, optional): Policy to use when handling nans. Defaults to
            "none".

            * 'none', 'propagate': If a NaN exists anywhere on the given dimension,
                return nans for that whole dimension.
            * 'raise': If a NaN exists at all in the datasets, raise an error.
            * 'omit', 'drop': If a NaN exists in `x` or `y`, drop that index and
                compute the slope without it.

    Returns:
        xarray object: ``y`` with linear fit removed.
    """
    return rm_poly(x, y, 1, dim=dim, nan_policy=nan_policy)


@is_xarray(0)
def autocorr(ds, lag=1, dim="time", return_p=False):
    """
    Calculated lagged correlation of a xr.Dataset.
    Parameters
    ----------
    ds : xarray dataset/dataarray
    lag : int (default 1)
        number of time steps to lag correlate.
    dim : str (default 'time')
        name of time dimension/dimension to autocorrelate over
    return_p : boolean (default False)
        if false, return just the correlation coefficient.
        if true, return both the correlation coefficient and p-value.
    Returns
    -------
    r : Pearson correlation coefficient
    p : (if return_p True) p-value
    """
    return st.autocorr(ds, lag=lag, dim=dim, return_p=return_p)


@is_xarray(0)
def ACF(ds, dim="time", nlags=None):
    """
    Compute the ACF of a time series to a specific lag.

    Args:
      ds (xarray object): dataset/dataarray containing the time series.
      dim (str): dimension to apply ACF over.
      nlags (optional int): number of lags to compute ACF over. If None,
                            compute for length of `dim` on `ds`.

    Returns:
      Dataset or DataArray with ACF results.

    Notes:
      This is preferred over ACF functions from MATLAB/scipy, since it doesn't
      use FFT methods.
    """
    # Drop variables that don't have requested dimension, so this can be
    # applied over the full dataset.
    if isinstance(ds, xr.Dataset):
        dropVars = [i for i in ds if dim not in ds[i].dims]
        ds = ds.drop(dropVars)

    # Loop through every step in `dim`
    if nlags is None:
        nlags = ds[dim].size

    acf = []
    # The 2 factor accounts for fact that time series reduces in size for
    # each lag.
    for i in range(nlags - 2):
        res = autocorr(ds, lag=i, dim=dim)
        acf.append(res)
    acf = xr.concat(acf, dim=dim)
    return acf


@is_xarray(0)
def corr(x, y, dim="time", lead=0, return_p=False):
    """
    Computes the Pearson product-momment coefficient of linear correlation.

    This version calculates the effective degrees of freedom, accounting
    for autocorrelation within each time series that could fluff the
    significance of the correlation.

    Parameters
    ----------
    x, y : xarray DataArray
        time series being correlated (can be multi-dimensional)
    dim : str (default 'time')
        Correlation dimension
    lead : int (default 0)
        If lead > 0, x leads y by that many time steps.
        If lead < 0, x lags y by that many time steps.
    return_p : boolean (default False)
        If true, return both r and p

    Returns
    -------
    r : correlation coefficient
    p : p-value accounting for autocorrelation (if return_p True)

    References (for dealing with autocorrelation):
    ----------
    1. Wilks, Daniel S. Statistical methods in the atmospheric sciences.
    Vol. 100. Academic press, 2011.
    2. Lovenduski, Nicole S., and Nicolas Gruber. "Impact of the Southern
    Annular Mode on Southern Ocean circulation and biology." Geophysical
    Research Letters 32.11 (2005).
    3. Brady, R. X., Lovenduski, N. S., Alexander, M. A., Jacox, M., and
    Gruber, N.: On the role of climate modes in modulating the air-sea CO2
    fluxes in Eastern Boundary Upwelling Systems, Biogeosciences Discuss.,
    https://doi.org/10.5194/bg-2018-415, in review, 2018.
    """
    # Broadcasts a time series to the same coordinates/size as the grid. If they
    # are both grids, this function does nothing and isn't expensive.
    x, y = xr.broadcast(x, y)

    # Negative lead should have y lead x.
    if lead < 0:
        lead = np.abs(lead)
        return st.corr(y, x, dim=dim, lag=lead, return_p=return_p)
    else:
        return st.corr(x, y, dim=dim, lag=lead, return_p=return_p)
