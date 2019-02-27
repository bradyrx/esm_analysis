import numpy as np
import xarray as xr
import warnings

from .stats import linregress


def co2_sol(t, s):
    """
    Compute CO2 sollubility per the equation used in CESM. The mean will be taken over
    the time series provided to produce the average solubility over this time period.
    Thus, if you want more accurate solubility you can feed in smaller time periods.

    Input
    -----
    t : SST time series (degC)
    s : SSS time series (PSU)

    Return
    ------
    ff : Value of solubility in mol/kg/atm

    References
    ----------
    Weiss & Price (1980, Mar. Chem., 8, 347-359;
    Eq 13 with table 6 values)
    """
    a = [-162.8301, 218.2968, 90.9241, -1.47696]
    b = [0.025695, -0.025225, 0.0049867]
    t = (np.mean(t) + 273.15) * 0.01
    s = np.mean(s)
    t_sq = t**2
    t_inv = 1.0 / t
    log_t = np.log(t)
    d0 = b[2] * t_sq + b[1] * t + b[0]
    # Compute solubility in mol.kg^{-1}.atm^{-1}
    ff = np.exp(a[0] + a[1] * t_inv + a[2] * log_t +
                a[3] * t_sq + d0 * s)
    return ff


def schmidt(t):
    """
    Computes the dimensionless Schmidt number. The mean will be taken over the
    time series provided to produce the average Schmidt number over this time period.
    The polynomials used are for SST ranges between 0 and 30C and a salinity of 35.

    Input
    -----
    t : SST time series (degC)

    Return
    ------
    Sc : Schmidt number (dimensionless)

    Reference
    --------
    Sarmiento and Gruber (2006). Ocean Biogeochemical Dynamics.
    Table 3.3.1
    """
    c = [2073.1, 125.62, 3.6276, 0.043219]
    t = np.mean(t)
    Sc = c[0] - c[1] * t + c[2] * (t ** 2) - c[3] * (t ** 3)
    return Sc


def temp_decomp_takahashi(ds, time_dim='time', temperature='tos', pco2='spco2'):
    """
    Decompose spco2 into thermal and non-thermal component.

    Reference
    ---------
    Takahashi, Taro, Stewart C. Sutherland, Colm Sweeney, Alain Poisson, Nicolas
        Metzl, Bronte Tilbrook, Nicolas Bates, et al. “Global Sea–Air CO2 Flux
        Based on Climatological Surface Ocean PCO2, and Seasonal Biological and
        Temperature Effects.” Deep Sea Research Part II: Topical Studies in
        Oceanography, The Southern Ocean I: Climatic Changes in the Cycle of
        Carbon in the Southern Ocean, 49, no. 9 (January 1,2002): 1601–22.
        https://doi.org/10/dmk4f2.

    Input
    -----
    ds : xr.Dataset containing spco2[ppm] and tos[C or K]

    Output
    ------
    thermal, non_thermal : xr.DataArray
        thermal and non-thermal components in ppm units

    """
    fac = 0.0432
    tos_mean = ds[temperature].mean(time_dim)
    tos_diff = ds[temperature] - tos_mean
    thermal = ds[pco2].mean(time_dim) * (np.exp(tos_diff * fac))
    non_thermal = ds[pco2] * (np.exp(tos_diff * -fac))
    return thermal, non_thermal


def potential_pco2(t_insitu, pco2_insitu):
    """
    Calculate potential pco2 in the inner ocean. Requires the first index of
    depth to be at the surface.

    Input
    -----
    t_insitu : xr object
        SST with depth [C or K]
    pco2_insitu : xr object
        pCO2 with depth [ppm]

    Output
    ------
    pco2_potential : xr object
        potential pco2 with depth

    Reference:
    - Sarmiento, Jorge Louis, and Nicolas Gruber. Ocean Biogeochemical Dynamics.
        Princeton, NJ: Princeton Univ. Press, 2006., p.421, eq. (10:3:1)

    """
    t_sfc = t_insitu.isel(depth=0)
    pco2_potential = pco2_insitu * (1 + 0.0423 * (t_sfc - t_insitu))
    return pco2_potential


def spco2_sensitivity(ds):
    """Generate sensitivities in spco2 for changes in other variables.

    * Lovenduski, Nicole S., Nicolas Gruber, Scott C. Doney, and Ivan D. Lima.
        “Enhanced CO2 Outgassing in the Southern Ocean from a Positive Phase of
        the Southern Annular Mode.” Global Biogeochemical Cycles 21, no. 2
        (2007). https://doi.org/10/fpv2wt.
    * Gruber and Sarmiento, 2005

    Args:
        ds (xr.Dataset): containing cmorized variables:
                            spco2 [ppm]: pCO2,ocean at ocean surface
                            talkos[mmol m-3]: Alkalinity at ocean surface
                            dissicos[mmol m-3]: DIC at ocean surface
                            tos [C] : temperature at ocean surface
                            sos [psu] : salinity at ocean surface

    Returns:
        sensitivity (xr.Dataset):

    """
    def _check_variables(ds):
        requiredVars = ['spco2', 'tos', 'sos', 'talkos', 'dissicos']
        if not all(i in ds.data_vars for i in requiredVars):
            missingVars = [i for i in requiredVars if i not in ds.data_vars]
            raise ValueError(f"""Missing variables needed for calculation:
            {missingVars}""")

    _check_variables(ds)
    # Sensitivities are based on the time-mean for each field. This computes
    # sensitivities at each grid cell.
    # TODO: Add keyword for sliding mean, as in N year chunks of time to
    # account for trends.
    DIC = ds['dissicos'].mean('time')
    ALK = ds['talkos'].mean('time')
    SALT = ds['sos'].mean('time')
    pCO2 = ds['spco2'].mean('time')

    buffer_factor = dict()
    buffer_factor['ALK'] = -ALK**2 / ((2 * DIC - ALK) * (ALK - DIC))
    buffer_factor['DIC'] = (3*ALK*DIC - 2*DIC**2) / \
                           ((2 * DIC - ALK) * (ALK - DIC))
    # Compute sensitivities
    sensitivity = dict()
    sensitivity['tos'] = 0.0423
    sensitivity['sos'] = 1 / SALT
    sensitivity['talkos'] = (1 / ALK) * buffer_factor['ALK']
    sensitivity['dissicos'] = (1 / DIC) * buffer_factor['DIC']
    sensitivity = xr.Dataset(sensitivity) * pCO2
    return sensitivity


# TODO: adapt for CESM and MPI output.
def spco2_decomposition_index(ds_terms, index, plot=False, **plot_kwargs):
    """Decompose oceanic surface pco2 in a first order Taylor-expansion.

    Reference:
    * Lovenduski, Nicole S., Nicolas Gruber, Scott C. Doney, and Ivan D. Lima.
        “Enhanced CO2 Outgassing in the Southern Ocean from a Positive Phase of
        the Southern Annular Mode.” Global Biogeochemical Cycles 21, no. 2
        (2007). https://doi.org/10/fpv2wt.

    Args:
        ds (xr.Dataset): containing cmorized variables:
                            spco2 [ppm]: pCO2,ocean at ocean surface
                            talkos[mmol m-3]: Alkalinity at ocean surface
                            dissicos[mmol m-3]: DIC at ocean surface
                            tos [C] : temperature at ocean surface
                            sos [psu] : salinity at ocean surface
        index (xr.object): Any timeseries.
        plot (bool): quick plot. Defaults to False.
        **plot_kwargs (type): `**plot_kwargs`.

    Returns:
        terms_in_pCO2_units (xr.Dataset): terms of spco2 decomposition,
                                          if `not plot`

    """
    warnings.warn("""Make sure your terms and index are detrended and
    deseasonalized for the most accurate results.""")

    pco2_sensitivity = spco2_sensitivity(ds_terms)
    ds_terms_anomaly = ds_terms - ds_terms.mean('time')

    def regression_against_index(ds, index, psig=None):
        terms = dict()
        for term in ds.data_vars:
            if term != 'spco2':
                print('Progress ...', term)
                reg = linregress(index, ds[term], psig=psig)
                terms[term] = reg['slope']
        terms = xr.Dataset(terms)
        return terms

    terms = regression_against_index(ds_terms_anomaly, index)
    terms_in_pCO2_units = terms * pco2_sensitivity
    if plot:
        terms_in_pCO2_units.to_array().plot(
            col='variable', cmap='RdBu_r', robust=True, **plot_kwargs)
    else:
        return terms_in_pCO2_units


def spco2_decomposition(ds_terms):
    """Decompose oceanic surface pco2 in a first order Taylor-expansion.

    Reference:
    * Lovenduski, Nicole S., Nicolas Gruber, Scott C. Doney, and Ivan D. Lima.
        “Enhanced CO2 Outgassing in the Southern Ocean from a Positive Phase of
        the Southern Annular Mode.” Global Biogeochemical Cycles 21, no. 2
        (2007). https://doi.org/10/fpv2wt.

    Args:
        ds_terms (xr.Dataset): containing cmorized variables:
                               spco2 [ppm]: pCO2,ocean at ocean surface
                               talkos[mmol m-3]: Alkalinity at ocean surface
                               dissicos[mmol m-3]: DIC at ocean surface
                               tos [C] : temperature at ocean surface
                               sos [psu] : salinity at ocean surface

    Returns:
        terms_in_pCO2_units (xr.Dataset): terms of spco2 decomposition

    """
    pco2_sensitivity = spco2_sensitivity(ds_terms)
    ds_terms_anomaly = ds_terms - ds_terms.mean('time')
    terms_in_pCO2_units = pco2_sensitivity * ds_terms_anomaly
    return terms_in_pCO2_units
