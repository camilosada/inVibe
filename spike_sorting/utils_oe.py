# Tools for pre-processing OpenEphis data
from open_ephys.analysis import Session
from spike_sorting import config
import json
import numpy as np
import pandas as pd
import h5py
from scipy.signal import butter, lfilter
import logging


def load_op_data(directory, n_node, recording_num):
    """Load OpenEphis data"""
    session = Session(directory)
    recordnode = session.recordnodes[n_node]
    # Load continuous data
    continuous = recordnode.recordings[recording_num].continuous[0]
    # Load events
    events = recordnode.recordings[recording_num].events
    # print(session)
    return session, recordnode, continuous, events


def load_data(bhv_path, spike_path, config_file):
    # Load behavioral data
    logging.info("Loading bhv data")
    bhv = h5py.File(bhv_path, "r")["ML"]
    # Load OpenEphis data
    logging.info("Loading OE data")
    directory = bhv_path.parents[3]
    _, _, continuous, events = load_op_data(
        directory, config.N_NODE, config.RECORDING_NUM
    )
    # Load spikes data
    logging.info("Loading spikes data")
    idx_spiketimes = np.load(str(spike_path) + "/spike_times.npy", "r").reshape(-1)
    spiketimes_clusters_id = np.load(
        str(spike_path) + "/spike_clusters.npy", "r"
    )  # to which neuron the spike times belongs to
    cluster_info = pd.read_csv(
        str(spike_path) + "/cluster_info.tsv", sep="\t"
    )  # info of each cluster

    f = open(config_file)
    config_data = json.load(f)
    f.close()
    return (
        bhv,
        continuous,
        events,
        idx_spiketimes,
        spiketimes_clusters_id,
        cluster_info,
        config_data,
    )


def signal_downsample(x, downsample, idx_start=0, axis=0):

    idx_ds = np.arange(idx_start, x.shape[axis], downsample)
    if axis == 0:
        return x[idx_ds]

    return x[:, idx_ds]


def butter_lowpass_filter(data, fc, fs, order=5, downsample=30):

    b, a = butter(N=order, Wn=fc, fs=fs, btype="low", analog=False)
    y = np.zeros((data.shape[1], int(np.floor(data.shape[0] / downsample)) + 1))

    for i_data in range(data.shape[1]):
        y_f = lfilter(b, a, data[:, i_data])
        y[i_data] = signal_downsample(y_f, downsample, idx_start=0, axis=0)
    return y


def select_timestamps(
    c_timestamps, e_timestamps, idx_spiketimes, fs, t_before_event=10, downsample=30
):
    # Select the timestamps of continuous data from t sec before the first event occurs
    # This is done to reduce the data
    start_time = np.where(c_timestamps == e_timestamps[0])[0]
    start_time = (
        start_time[0] if start_time.shape[0] > 0 else 0
    )  # check if not empty, else we select all data
    start_time = (
        start_time - fs * t_before_event if start_time - fs * t_before_event > 0 else 0
    )  # check if start_time - fs*t >0, else we select all data
    # select timestamps from start_time and donwsample
    filtered_timestamps = signal_downsample(c_timestamps, downsample, start_time)

    spiketimes = c_timestamps[idx_spiketimes]  # timestamps of all the spikes

    return filtered_timestamps, start_time, spiketimes


def reconstruct_8bits_words(real_strobes, e_channel, e_state):
    idx_old = 0
    current_8code = np.zeros(8, dtype=np.int64)
    full_word = np.zeros(len(real_strobes))

    for n_strobe, idx_strobe in enumerate(real_strobes):

        for ch in np.arange(0, 7):

            idx_ch = np.where(e_channel[idx_old:idx_strobe] == ch + 1)[0]

            current_8code[7 - ch] = (
                e_state[idx_ch[-1]] if idx_ch.size != 0 else current_8code[7 - ch]
            )

        full_word[n_strobe] = int("".join([str(item) for item in current_8code]), 2)

    return full_word


def check_strobes(bhv, full_word, real_strobes):
    # Check if strobe and codes number match
    bhv_codes = []
    trials = list(bhv.keys())[1:-1]
    for i_trial in trials:
        bhv_codes.append(list(bhv[i_trial]["BehavioralCodes"]["CodeNumbers"])[0])
    bhv_codes = np.concatenate(bhv_codes)

    if full_word.shape[0] != real_strobes.shape[0]:
        logging.info("Warning, Strobe and codes number do not match")
        logging.info("Strobes =", real_strobes.shape[0])
        logging.info("codes number =", full_word.shape[0])
    else:
        logging.info("Strobe and codes number do match")
        logging.info("Strobes = %d", real_strobes.shape[0])
        logging.info("codes number = %d", full_word.shape[0])

    if full_word.shape[0] != bhv_codes.shape[0]:
        logging.info("Warning, ML and OE code numbers do not match")
    else:
        logging.info("ML and OE code numbers do match")
        if np.sum(bhv_codes - full_word) != 0:
            logging.info("Warning, ML and OE codes are different")
        else:
            logging.info("ML and OE codes are the same")


def find_events_codes(events, bhv):

    # Reconstruct 8 bit words
    logging.info("Reconstructing 8 bit words")
    idx_real_strobes = np.where(
        np.logical_and(
            np.logical_and(events.channel == 8, events.state == 1), events.timestamp > 0
        )
    )[
        0
    ]  # state 1: ON, state 0: OFF
    full_word = reconstruct_8bits_words(
        idx_real_strobes, e_channel=events.channel, e_state=events.state
    )
    # Check if strobe and codes number match
    check_strobes(bhv, full_word, idx_real_strobes)

    real_strobes = events.timestamp[idx_real_strobes].values
    start_trials = real_strobes[
        full_word == config.START_CODE
    ]  # timestamps where trials starts
    end_trials = real_strobes[full_word == config.END_CODE]

    # search number of blocks
    trial_keys = list(bhv.keys())[1:-1]
    n_trials = len(trial_keys)
    blocks = []
    # iterate over trials
    for trial_i in range(n_trials):
        blocks.append(bhv[trial_keys[trial_i]]["Block"][0][0])

    bl_start_trials = []
    bl_end_trials = []

    for block in np.unique(blocks):
        idx_block = np.where(blocks == block)[0]

        bl_start_trials.append(start_trials[idx_block])
        bl_end_trials.append(end_trials[idx_block])

    return (
        full_word,
        real_strobes,
        bl_start_trials,
        bl_end_trials,
        np.unique(blocks),
    )


def compute_lfp(c_samples, start_time):
    # Compute LFP

    LFP_ds = butter_lowpass_filter(
        c_samples[start_time:, :-2],
        fc=config.FC,
        fs=config.FS,
        order=config.ORDER,
        downsample=config.DOWNSAMPLE,
    )
    eyes_ds = signal_downsample(
        c_samples[start_time:, -2:].reshape(2, -1),
        config.DOWNSAMPLE,
        idx_start=0,
        axis=1,
    )
    return LFP_ds, eyes_ds
