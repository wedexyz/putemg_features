from . import biolab_utilities

from .pyeeg import pyeeg

import pandas as pd
import numpy as np
from numpy.lib.stride_tricks import as_strided

from scipy import stats, signal
from scipy import interpolate

import xml.etree.ElementTree as ET

import time


def calculate_feature(record: pd.DataFrame, name, **kwargs):
    """
    Calculates feature given name of given pandas.DataFrame. Feature is calculated for each Series with
    column name of "EMG_\d". Feature parameters are passed by **kwargs, eg. window=500, step=250
    :param record: pandas.DataFrame - input DataFrame with data to calculate features from
    :param name: string - name of the requested feature
    :param kwargs: parameters for feature calculation.
    :return: pandas.DataFrame - DataFrame containing output of desired feature
    """
    feature_func_name = 'feature_' + name.lower()  # Get feature function name based on name
    feature_values = pd.DataFrame()  # Create empty DataFrame

    start = time.time()
    print('Calculating feature ' + name + ':', end='', flush=True)
    for column in record.filter(regex=r"EMG_\d+"):  # For each column containing EMG data (for each Series)
        print(' ' + column.split('_')[1], end='', flush=True)
        feature_label = name + '_' + column.split('_')[1]  # Prepare feature column label
        # Call feature calculation by function name, and add to output DataFrame
        feature = globals()[feature_func_name](record[column], **kwargs)
        if isinstance(feature, pd.Series):
            feature_values[feature_label] = feature
        elif isinstance(feature, pd.DataFrame):
            d = {}
            for c in feature.columns:
                d[c] = feature_label + "_" + c
            feature = feature.rename(columns=d)
            feature_values = feature_values.join(feature, how='outer')

    print('', flush=True)
    print("Elapsed time: {:.2f}s".format(time.time() - start))

    return feature_values


def calculate_force_feature(record: pd.DataFrame, name, **kwargs):
    feature_func_name = 'force_feature_' + name.lower()  # Get feature function name based on name
    feature_values = pd.DataFrame()  # Create empty DataFrame

    start = time.time()
    print('Calculating force feature ' + name + ':', end='', flush=True)
    for column in record.filter(regex=r"FORCE_\d+"):  # For each column containing EMG data (for each Series)
        print(' ' + column.split('_')[1], end='', flush=True)
        feature_label = 'FORCE_' + name + '_' + column.split('_')[1]  # Prepare feature column label
        # Call feature calculation by function name, and add to output DataFrame
        feature = globals()[feature_func_name](record[column], **kwargs)
        if isinstance(feature, pd.Series):
            feature_values[feature_label] = feature
        elif isinstance(feature, pd.DataFrame):
            d = {}
            for c in feature.columns:
                d[c] = feature_label + "_" + c
            feature = feature.rename(columns=d)
            feature_values = feature_values.join(feature, how='outer')

    print('', flush=True)
    print("Elapsed time: {:.2f}s".format(time.time() - start))

    return feature_values


def features_from_xml(xml_file_url, hdf5_file_url):
    """
    Calculates feature defined in given XML file containing feature names and parameters. See 'all_features.xml' for
    example. Calculate features of given putEMG record file in hdf5 format.
    :param xml_file_url: string - url to XML file containing feature descriptors
    :param hdf5_file_url: string - url to putEMG hdf5 record file
    :return: pandas.DataFrame - DataFrame containing output for all desired features
    """
    record: pd.DataFrame = pd.read_hdf(hdf5_file_url)  # Read HDF5 file into pandas DataFrame

    return features_from_xml_on_df(xml_file_url, record)


def features_from_xml_on_df(xml_file_url, record: pd.DataFrame):
    feature_frame = pd.DataFrame()

    xml_root = ET.parse(xml_file_url).getroot()  # Load XML file with feature config

    windowing_entry = list(xml_root.iter('windowing'))[0]
    windowing_options = biolab_utilities.convert_types_in_dict(windowing_entry.attrib)

    for xml_entry in xml_root.iter('feature'):  # For each feature entry in XML file
        # Convert attribute dictionary to Python literals
        xml_entry.attrib = biolab_utilities.convert_types_in_dict(xml_entry.attrib)
        # add to output frame values calculated by each feature function
        feature_frame = feature_frame.join(calculate_feature(record, **xml_entry.attrib,
                                                             window=windowing_options['window'],
                                                             step=windowing_options['step']), how="outer")

    for xml_entry in xml_root.iter('force_feature'):  # For each force feature entry in XML file
        # Convert attribute dictionary to Python literals
        xml_entry.attrib = biolab_utilities.convert_types_in_dict(xml_entry.attrib)
        # add to output frame values calculated by each feature function
        feature_frame = feature_frame.join(calculate_force_feature(record, **xml_entry.attrib,
                                                                   window=windowing_options['window'],
                                                                   step=windowing_options['step']), how="outer")

    if len(list(xml_root.iter('force_feature'))):
        re = "(^(?!EMG_|FORCE_).*)"
    else:
        re = "^(?!EMG_).*"

    for other_data in list(record.filter(regex=re)):
        feature_frame[other_data] = record.loc[feature_frame.index, other_data]

    return feature_frame


def feature_iav(series, window, step):
    """Integral Absolute Value"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(np.abs(windows_strided), axis=1), index=series.index[indexes])


def feature_aac(series, window, step):
    """Average Amplitude Change"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.divide(np.sum(np.abs(np.diff(windows_strided)), axis=1), window),
                     index=series.index[indexes])


def feature_apen(series, window, step, m, r):
    """Approximate Entropy
    AnEn feature is using PyEEG library v0.4.0 as it is, licensed with GNU GPL v3
    http://pyeeg.org"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.apply_along_axis(lambda win: pyeeg.ap_entropy(win, m, r),
                                              axis=1, arr=windows_strided), index=series.index[indexes])


def feature_ar(series, window, step, order) -> pd.DataFrame:
    """Auto-Regressive Coefficients"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)

    column_names = [str(i) for i in range(0, order)]
    win_coefs = pd.DataFrame(index=series.index[indexes], columns=column_names, dtype=np.float64)

    for widx in range(len(windows_strided)):
        stride = windows_strided[widx].strides[0]
        stride_count = len(windows_strided[widx]) - order
        x = as_strided(windows_strided[widx], shape=[stride_count, order], strides=(stride, stride))
        y = windows_strided[widx][order:]

        a, _, _, _ = np.linalg.lstsq(x, y, rcond=None)

        win_coefs.loc[series.index[indexes[widx]], :] = a
    return win_coefs


def feature_cc(series, window, step, order):
    """Cepstral Coefficients"""
    win_coefs = feature_ar(series, window, step, order)
    coefs = win_coefs.values
    coefs[:, 0] = -coefs[:, 0]
    for r in range(0, coefs.shape[0]):
        for p in range(1, order):
            coefs[r, p] = -coefs[r, p] - np.sum(
                [1 - (l / (p + 1)) for l in range(1, p + 1)] * np.full(p, coefs[r, p] * coefs[r, p - 1]))
    win_coefs.loc[:, :] = coefs
    return win_coefs


def feature_dasdv(series, window, step):
    """Difference Absolute Standard Deviation Value"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sqrt(np.mean(np.square(np.diff(windows_strided)), axis=1)), index=series.index[indexes])


def feature_kurt(series, window, step):
    """Kurtosis"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=stats.kurtosis(windows_strided, axis=1), index=series.index[indexes])


def feature_log(series, window, step):
    """Log Detector"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.exp(np.mean(np.log(np.abs(windows_strided)), axis=1)), index=series.index[indexes])


def feature_mav1(series, window, step):
    """Modified Mean Absolute Value Type 1"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    win_weight = [1 if ((0.25*window <= i) & (i <= 0.75*window)) else 0.5 for i in range(1, window+1)]
    return pd.Series(data=np.mean(np.abs(windows_strided) * win_weight, axis=1), index=series.index[indexes])


def feature_mav2(series, window, step):
    """Modified Mean Absolute Value Type 2"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    win_weight = biolab_utilities.window_trapezoidal(window, 0.25)
    return pd.Series(data=np.mean(np.abs(windows_strided) * win_weight, axis=1), index=series.index[indexes])


def feature_mav(series, window, step):
    """Mean Absolute Value"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.mean(np.abs(windows_strided), axis=1), index=series.index[indexes])


def feature_mavslp(series, window, step):
    """Mean Absolute Value Slope"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.diff(np.mean(np.abs(windows_strided), axis=1)), index=series.index[indexes[1:]])


def feature_mhw(series, window, step):
    """Multiple Hamming Windows"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(np.square(windows_strided * np.hamming(window)), axis=1), index=series.index[indexes])


def feature_mtw(series, window, step, windowslope):
    """Multiple Trapezoidal Windows"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(np.square(windows_strided) * biolab_utilities.window_trapezoidal(window, windowslope),
                                 axis=1),
                     index=series.index[indexes])


def feature_myop(series, window, step, threshold):
    """Myopulse Percentage Rate"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(windows_strided > threshold, axis=1) / window, index=series.index[indexes])


def feature_rms(series, window, step):
    """Root Mean Square"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sqrt(np.mean(np.square(windows_strided), axis=1)), index=series.index[indexes])


def feature_sampleen(series, window, step, m, r):
    """Sample Entropy
    SampEn feature is using PyEEG library v 0.02_r2 as it is, licensed with GNU GPL v3
    http://pyeeg.sourceforge.net/"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.apply_along_axis(lambda win: pyeeg.samp_entropy(win, m, r), axis=1, arr=windows_strided),
                     index=series.index[indexes])


def feature_skew(series, window, step):
    """Skewness"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=stats.skew(windows_strided, axis=1), index=series.index[indexes])


def feature_ssc(series, window, step, threshold):
    """Slope Sign Change"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.apply_along_axis(lambda x: np.sum((np.diff(x[:-1]) * np.diff(x[1:])) <= -threshold),
                                              axis=1, arr=windows_strided), index=series.index[indexes])


def feature_ssi(series, window, step):
    """Simple Square Integral"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(np.square(windows_strided), axis=1), index=series.index[indexes])


def feature_tm(series, window, step, order):
    """Absolute Temporal Moment"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.abs(np.mean(np.power(windows_strided, order), axis=1)), index=series.index[indexes])


def feature_var(series, window, step):
    """Variance"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.var(windows_strided, axis=1), index=series.index[indexes])


def feature_v(series, window, step, v):
    """V-Order"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.power(np.abs(np.mean(np.power(windows_strided, v), axis=1)), 1./v),
                     index=series.index[indexes])


def feature_wamp(series, window, step, threshold):
    """Willison Amplitude"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(np.diff(windows_strided) >= threshold, axis=1), index=series.index[indexes])


def feature_wl(series, window, step):
    """Waveform Length"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.sum(np.diff(windows_strided), axis=1), index=series.index[indexes])


def feature_zc(series, window, step, threshold):
    """Zero Crossing"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    zc = np.apply_along_axis(lambda x: np.sum(np.diff(x[(x < -threshold) | (x > threshold)] > 0)), axis=1,
                             arr=windows_strided)
    return pd.Series(data=zc, index=series.index[indexes])


def feature_mnf(series, window, step):
    """Mean Frequency"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    return pd.Series(data=np.sum(power*freq, axis=1) / np.sum(power, axis=1), index=series.index[indexes])


def feature_mdf(series, window, step):
    """Median Frequency"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    ttp_half = np.sum(power, axis=1)/2
    mdf = np.zeros(len(windows_strided))
    for w in range(len(power)):
        for s in range(1, len(power) + 1):
            if np.sum(power[w, :s]) > ttp_half[w]:
                mdf[w] = freq[s - 1]
                break
    return pd.Series(data=mdf, index=series.index[indexes])


def feature_pkf(series, window, step):
    """Peak Frequency"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    return pd.Series(data=freq[np.argmax(power, axis=1)], index=series.index[indexes])


def feature_mnp(series, window, step):
    """Mean Power"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    return pd.Series(data=np.mean(power, axis=1), index=series.index[indexes])


def feature_ttp(series, window, step):
    """Total Power"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    return pd.Series(data=np.sum(power, axis=1), index=series.index[indexes])


def feature_sm(series, window, step, order):
    """Spectral Moment"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    return pd.Series(data=np.sum(power * np.power(freq, order), axis=1), index=series.index[indexes])


def feature_fr(series, window, step, flb, fhb):
    """Frequency Ratio"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    lb = np.sum(power[:, (flb[0] < freq) & (freq < flb[1])], axis=1)
    hb = np.sum(power[:, (fhb[0] < freq) & (freq < fhb[1])], axis=1)
    return pd.Series(data=(lb / hb), index=series.index[indexes])


def feature_vcf(series, window, step):
    """Variance of Central Frequency"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)

    def sm(order):
        return np.sum(power * np.power(freq, order), axis=1)

    return pd.Series(data=sm(2)/sm(0) - np.square(sm(1)/sm(0)), index=series.index[indexes])


def feature_psr(series, window, step, n):
    """Power Spectrum Ratio"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    PKF_id = np.argmax(power, axis=1)
    lb = np.where(PKF_id - 20 < 0, 0, PKF_id - 20)
    hb = np.where(PKF_id + 20 > window, window, PKF_id + 20)
    return pd.Series(data=[sum(p[l:h]) for p, l, h in zip(power, lb, hb)] / np.sum(power, axis=1),
                     index=series.index[indexes])


def feature_snr(series, window, step, powerband, noiseband):
    """Signal-to-Noise Ratio"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    snr = np.apply_along_axis(lambda p:
                              np.sum(p[(freq > powerband[0]) & (freq < powerband[1])]) /
                              (np.sum(p[(freq > noiseband[0]) & (freq < noiseband[1])]) * np.max(freq)),
                              axis=1, arr=power)
    return pd.Series(data=snr, index=series.index[indexes])


def feature_dpr(series, window, step, band, n):
    """Maximum-to-minimum Drop in Power Density Ratio"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)

    dpr = pd.Series()
    for pidx in range(len(power)):
        power_b = power[pidx][(freq > band[0]) & (freq < band[1])]
        stride = power_b.strides[0]
        stride_count = len(power_b) - n + 1
        p_strided = as_strided(power_b, shape=[stride_count, n], strides=(stride, stride))
        means = np.mean(p_strided, axis=1)
        dpr.at[series.index[indexes[pidx]]] = np.max(means) / np.min(means)

    return pd.Series(data=dpr, index=series.index[indexes])


def feature_ohm(series, window, step):
    """Power Spectrum Deformation"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)

    def sm(order):
        return np.sum(power * np.power(freq, order), axis=1)

    return pd.Series(data=np.sqrt(sm(2)/sm(0)) / (sm(1)/sm(0)), index=series.index[indexes])


def feature_max(series, window, step, order, cutoff):
    """Maximum Amplitude"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    fs = 5120
    b, a = signal.butter(order, cutoff / (0.5 * fs), btype='lowpass', analog=False, output='ba')
    return pd.Series(data=np.max(signal.lfilter(b, a, np.abs(windows_strided), axis=1), axis=1),
                     index=series.index[indexes])


def feature_smr(series, window, step, n):
    """Signal-to-Motion Artifact Ratio"""
    # TODO: Verification Needed
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)

    freq_over35 = freq > 35
    freq_over35_idx = np.argmax(freq_over35)

    smr = pd.Series()
    for pidx in range(len(power)):
        power_b = power[pidx][freq_over35]
        stride = power_b.strides[0]
        stride_count = len(power_b) - n + 1
        p_strided = as_strided(power_b, shape=[stride_count, n], strides=(stride, stride))
        mean = np.mean(p_strided, axis=1)
        max = np.max(mean)
        max_idx = np.argmax(mean) + int(np.floor(n / 2.0)) + freq_over35_idx
        a = max / freq[max_idx]

        smr.at[series.index[indexes[pidx]]] =\
            np.sum(power[pidx][freq < 600]) / np.sum(power[pidx][power[pidx] > (freq*a)])

    return pd.Series(data=smr, index=series.index[indexes])


def box_counting_dimension(sig, y_box_size_multiplier, subsampling):
    # Box-Counting Example:
    # https://gist.github.com/rougier/e5eafc276a4e54f516ed5559df4242c0#file-fractal-dimension-py-L25
    n = 2 ** np.floor(np.log(len(sig)) / np.log(2))
    n = int(np.log(n) / np.log(2))
    sizes = 2 ** np.arange(n, 1, -1)

    box_count = []
    for box_size in sizes:
        x_box_size = box_size
        y_box_size = box_size * y_box_size_multiplier

        sig_minimum = np.min(sig)

        box_occupation = np.zeros(
            [int(len(sig) / x_box_size) + 1, int((np.max(sig) - sig_minimum) / y_box_size) + 1])

        interp_func = interpolate.interp1d(np.arange(0, len(sig), 1), sig.reshape(1, len(sig))[0])
        x_interp = np.arange(0, len(sig) - 1 + 1 / subsampling, 1 / subsampling)
        sig_interp = interp_func(x_interp)

        for i in range(len(sig_interp)):
            x_box_id = int(x_interp[i] / x_box_size)
            y_box_id = int((sig_interp[i] - sig_minimum) / y_box_size)
            box_occupation[x_box_id, y_box_id] = 1

        box_count.append(np.sum(box_occupation))

    coefs = np.polyfit(np.log(1 / sizes), np.log(box_count), 1)
    return coefs[0]


def feature_bc(series, window, step, y_box_size_multiplier, subsampling):
    """Box-Counting Dimension"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.apply_along_axis(lambda sig:
                                              box_counting_dimension(sig, y_box_size_multiplier, subsampling),
                                              axis=1, arr=windows_strided), index=series.index[indexes])


def feature_psdfd(series, window, step, power_box_size_multiplier, subsampling):
    """Power Spectral Density Fractal Dimension"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    freq, power = signal.periodogram(windows_strided, 5120)
    return pd.Series(data=np.apply_along_axis(lambda sig:
                                              box_counting_dimension(sig, power_box_size_multiplier, subsampling),
                                              axis=1, arr=power), index=series.index[indexes])


def force_feature_mean(series, window, step):
    """Mean value"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.mean(windows_strided, axis=1), index=series.index[indexes])


def force_feature_median(series, window, step):
    """Median value"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=np.median(windows_strided, axis=1), index=series.index[indexes])

def force_feature_last(series, window, step):
    """Last value of the window - resampling"""
    windows_strided, indexes = biolab_utilities.moving_window_stride(series.values, window, step)
    return pd.Series(data=windows_strided[::, -1], index=series.index[indexes])
