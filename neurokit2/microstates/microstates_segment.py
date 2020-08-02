# -*- coding: utf-8 -*-
import numpy as np

from .microstates_clean import microstates_clean
from .microstates_classify import microstates_classify
from ..stats import cluster
from ..stats.cluster_quality import _cluster_quality_gev


def microstates_segment(eeg, n_microstates=4, train="gfp", method='kmod', gfp_method='l1', sampling_rate=None,
                        standardize_eeg=False, n_runs=10, max_iterations=1000, criterion='gev', random_state=None, **kwargs):
    """Segment a continuous M/EEG signal into microstates using different clustering algorithms.

    Several runs of the clustering algorithm are performed, using different random initializations.
    The run that resulted in the best segmentation, as measured by global explained variance
    (GEV), is used.

    The microstates clustering is typically fitted on the EEG data at the global field power (GFP)
    peaks to maximize the signal to noise ratio and focus on moments of high global neuronal
    synchronization. It is assumed that the topography around a GFP peak remains stable and is at
    its highest signal-to-noise ratio at the GFP peak.

    Parameters
    ----------
    eeg : np.ndarray
        An array (channels, times) of M/EEG data or a Raw or Epochs object from MNE.
    n_microstates : int
        The number of unique microstates to find. Defaults to 4.
    train : Union[str, int, float]
        Method for selecting the timepoints how which to train the clustering algorithm. Can be
        'gfp' to use the peaks found in the Peaks in the global field power. Can be 'all', in which
        case it will select all the datapoints. It can also be a number or a ratio, in which case
        it will select the corresponding number of evenly spread data points. For instance,
        ``train=10`` will select 10 equally spaced datapoints, whereas ``train=0.5`` will select
        half the data. See ``microstates_peaks()``.
    method : str
        The algorithm for clustering. Can be one of 'kmeans', the modified k-means algorithm 'kmod' (default),
        'pca' (Principal Component Analysis), 'ica' (Independent Component Analysis), or
        'aahc' (Atomize and Agglomerate Hierarchical Clustering) which is more computationally heavy.
    gfp_method : str
        The GFP extraction method, can be either 'l1' (default) or 'l2' to use the L1 or L2 norm.
        See ``nk.eeg_gfp()`` for more details.
    sampling_rate : int
        The sampling frequency of the signal (in Hz, i.e., samples/second).
    standardize_eeg : bool
        Standardized (z-score) the data across time prior to GFP extraction
        using ``nk.standardize()``.
    n_runs : int
        The number of random initializations to use for the k-means algorithm.
        The best fitting segmentation across all initializations is used.
        Defaults to 10.
    max_iterations : int
        The maximum number of iterations to perform in the k-means algorithm.
        Defaults to 1000.
    criterion : str
        Which criterion to use to choose the best run for modified k-means algorithm,
        can be 'gev' (default) which selects
        the best run based on the highest global explained variance, or 'cv' which selects the best run
        based on the lowest cross-validation criterion. See ``nk.microstates_gev()``
        and ``nk.microstates_crossvalidation()`` for more details respectively.
    random_state : Union[int, numpy.random.RandomState]
        The seed or ``RandomState`` for the random number generator. Defaults
        to ``None``, in which case a different seed is chosen each time this
        function is called.

    Returns
    -------
    dict
        Contains information about the segmented microstates:
        - **Microstates**: The topographic maps of the found unique microstates which has a shape of
        n_channels x n_states
        - **Sequence**: For each sample, the index of the microstate to which the sample has been assigned.
        - **GEV**: The global explained variance of the microstates.
        - **GFP**: The global field power of the data.
        - **Cross-Validation Criterion**: The cross-validation value of the iteration.
        - **Explained Variance**: The explained variance of each cluster map generated by PCA.
        - **Total Explained Variance**: The total explained variance of the cluster maps generated by PCA.

    Examples
    ---------
    >>> import neurokit2 as nk
    >>>
    >>> eeg = nk.mne_data("filt-0-40_raw").filter(1, 35)
    >>> eeg = nk.eeg_rereference(eeg, 'average')
    >>>
    >>> # Kmeans
    >>> out_kmeans = nk.microstates_segment(eeg, method='kmeans')
    >>> nk.microstates_plot(out_kmeans, gfp=out_kmeans["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # Modified kmeans
    >>> out_kmod = nk.microstates_segment(eeg, method='kmod')
    >>> nk.microstates_plot(out_kmod, gfp=out_kmod["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # PCA
    >>> out_pca = nk.microstates_segment(eeg, method='pca', standardize_eeg=True)
    >>> nk.microstates_plot(out_pca, gfp=out_pca["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # ICA
    >>> out_ica = nk.microstates_segment(eeg, method='ica', standardize_eeg=True)
    >>> nk.microstates_plot(out_ica, gfp=out_ica["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>
    >>>
    >>> # AAHC
    >>> out_aahc = nk.microstates_segment(eeg, method='aahc')
    >>> nk.microstates_plot(out_aahc, gfp=out_aahc["GFP"][0:500]) #doctest: +ELLIPSIS
    <Figure ...>


    See Also
    --------
    eeg_gfp, microstates_peaks, microstates_gev, microstates_crossvalidation, microstates_classify

    References
    ----------
    - Pascual-Marqui, R. D., Michel, C. M., & Lehmann, D. (1995). Segmentation of brain
    electrical activity into microstates: model estimation and validation. IEEE Transactions
    on Biomedical Engineering.

    """
    # Sanitize input
    data, indices, gfp, info_mne = microstates_clean(eeg,
                                                     train=train,
                                                     sampling_rate=sampling_rate,
                                                     standardize_eeg=standardize_eeg,
                                                     gfp_method=gfp_method,
                                                     **kwargs)

    # Run clustering algorithm
    if method in ["kmods", "kmod", "kmeans modified", "modified kmeans"]:

        # If no random state specified, generate a random state
        if not isinstance(random_state, np.random.RandomState):
            random_state = np.random.RandomState(random_state)

        # Generate one random integer for each run
        random_state = random_state.choice(range(n_runs * 1000), n_runs, replace=False)

        # Initialize values
        gev = 0
        microstates = None
        segmentation = None
        polarity = None
        info = None

        # Do several runs of the k-means algorithm, keep track of the best segmentation.
        for run in range(n_runs):

            # Run clustering on subset of data
            _, _, current_info = cluster(data[:, indices].T,
                                         method="kmod",
                                         n_clusters=n_microstates,
                                         random_state=random_state[run],
                                         max_iterations=max_iterations,
                                         threshold=1e-6)
            current_microstates = current_info["clusters_normalized"]

            # Run segmentation on the whole dataset
            s, p, g = _microstates_segment_runsegmentation(data, current_microstates, gfp)

            # If better (i.e., higher GEV), keep this segmentation
            if g > gev:
                microstates, segmentation, polarity, gev = current_microstates, s, p, g
                info = current_info


    else:
        # Run clustering algorithm on subset
        _, microstates, info = cluster(data[:, indices].T,
                                       method=method,
                                       n_clusters=n_microstates,
                                       random_state=random_state,
                                       **kwargs)

        # Run segmentation on the whole dataset
        segmentation, polarity, gev = _microstates_segment_runsegmentation(data, microstates, gfp)

    # Reorder
    segmentation, microstates  = microstates_classify(segmentation, microstates)

    # CLustering quality
#    quality = cluster_quality(data, segmentation, clusters=microstates, info=info, n_random=10, sd=gfp)

    # Output
    info = {"Microstates": microstates,
            "Sequence": segmentation,
            "GEV": gev,
            "GFP": gfp,
            "Polarity": polarity,
            "Info_algorithm": info,
            "Info": info_mne}

    return info


# =============================================================================
# Utils
# =============================================================================
def _microstates_segment_runsegmentation(data, microstates, gfp):
    # Find microstate corresponding to each datapoint
    activation = microstates.dot(data)
    segmentation = np.argmax(np.abs(activation), axis=0)
    polarity = np.sign(np.choose(segmentation, activation))

    # Get Global Explained Variance (GEV)
    gev = _cluster_quality_gev(data.T, microstates, segmentation, sd=gfp)
    return segmentation, polarity, gev
