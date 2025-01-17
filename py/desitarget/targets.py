"""
desitarget.targets
==================

Presumably this defines targets.
"""
import numpy as np
import healpy as hp
import numpy.lib.recfunctions as rfn

from astropy.table import Table

from desitarget.targetmask import desi_mask, bgs_mask, mws_mask
from desitarget.targetmask import scnd_mask, targetid_mask
from desitarget.targetmask import obsconditions

# ADM set up the DESI default logger.
from desiutil.log import get_logger
log = get_logger()

############################################################
# TARGETID bit packing

# Of 64 bits total:

# First 52 bits available for propagated targetid, giving a max value of
# 2**52 - 1 = 4503599627370495. The invididual sources are free to
# distribute these bits however they like

# For the case where the propagated ID encodes a filenumber and rownumber
# this would allow, for example, 1048575 files with 4294967295 rows per
# file.

# Higher order bits encode source file and survey. Seems pointless since source
# and survey are implcitiy in the *_TARGET columns which are propagated through
# fiberassign anyway, so this is just a toy example.

# Number of bits allocated to each section
USER_END = 52    # Free to use
SOURCE_END = 60  # Source class
SURVEY_END = 64  # Survey

# Bitmasks
ENCODE_MTL_USER_MASK = 2**USER_END - 2**0               # 0x000fffffffffffff
ENCODE_MTL_SOURCE_MASK = 2**SOURCE_END - 2**USER_END    # 0x0ff0000000000000
ENCODE_MTL_SURVEY_MASK = 2**SURVEY_END - 2**SOURCE_END  # 0xf000000000000000

# Maximum number of unique values
USER_MAX = ENCODE_MTL_USER_MASK                    # 4503599627370495
SOURCE_MAX = ENCODE_MTL_SOURCE_MASK >> USER_END    # 255
SURVEY_MAX = ENCODE_MTL_SURVEY_MASK >> SOURCE_END  # 15

TARGETID_SURVEY_INDEX = {'desi': 0, 'bgs': 1, 'mws': 2}


def target_bitmask_to_string(target_class, mask):
    """Converts integer values of target bitmasks to strings.

    Where multiple bits are set, joins the names of each contributing bit with
    '+'.
    """
    target_class_names = np.zeros(len(target_class), dtype=np.object)
    unique_target_classes = np.unique(target_class)
    for tc in unique_target_classes:
        # tc is the encoded integer value of the target bitmask
        has_this_target_class = np.where(target_class == tc)[0]

        tc_name = '+'.join(mask.names(tc))
        target_class_names[has_this_target_class] = tc_name
        log.info('Target class %s (%d): %d' % (tc_name, tc, len(has_this_target_class)))

    return target_class_names


def encode_mtl_targetid(targets):
    """
    Sets targetid used in MTL, which encode both the target class and
    arbitrary tracibility data propagated from individual input sources.

    Allows rows in final MTL (and hence fibre map) to be mapped to input
    sources.
    """
    encoded_targetid = targets['TARGETID'].copy()

    # Validate incoming target ids
    if not np.all(encoded_targetid <= ENCODE_MTL_USER_MASK):
        log.error('Invalid range of user-specfied targetid: cannot exceed {}'
                  .format(ENCODE_MTL_USER_MASK))

    desi_target = targets['DESI_TARGET'] != 0
    bgs_target = targets['BGS_TARGET'] != 0
    mws_target = targets['MWS_TARGET'] != 0

    # Assumes surveys are mutually exclusive.
    assert(np.max(np.sum([desi_target, bgs_target, mws_target], axis=0)) == 1)

    # Set the survey bits
    # encoded_targetid[desi_target] += TARGETID_SURVEY_INDEX['desi'] << SOURCE_END
    # encoded_targetid[bgs_target ] += TARGETID_SURVEY_INDEX['bgs']  << SOURCE_END
    # encoded_targetid[mws_target]  += TARGETID_SURVEY_INDEX['mws']  << SOURCE_END

    encoded_targetid[desi_target] += encode_survey_source(TARGETID_SURVEY_INDEX['desi'], 0, 0)
    encoded_targetid[bgs_target] += encode_survey_source(TARGETID_SURVEY_INDEX['bgs'], 0, 0)
    encoded_targetid[mws_target] += encode_survey_source(TARGETID_SURVEY_INDEX['mws'], 0, 0)

    # Set the source bits. Will be different for each survey.
    desi_sources = ['ELG', 'LRG', 'QSO']
    bgs_sources = ['BGS_FAINT', 'BGS_BRIGHT', 'BGS_WISE']
    mws_sources = ['MWS_MAIN', 'MWS_WD', 'MWS_NEARBY']

    for name in desi_sources:
        ii = (targets['DESI_TARGET'] & desi_mask[name]) != 0
        assert(desi_mask[name] <= SOURCE_MAX)
        encoded_targetid[ii] += encode_survey_source(0, desi_mask[name], 0)

    for name in bgs_sources:
        ii = (targets['BGS_TARGET'] & bgs_mask[name]) != 0
        assert(bgs_mask[name] <= SOURCE_MAX)
        encoded_targetid[ii] += encode_survey_source(0, bgs_mask[name], 0)

    for name in mws_sources:
        ii = (targets['MWS_TARGET'] & mws_mask[name]) != 0
        assert(mws_mask[name] <= SOURCE_MAX)
        encoded_targetid[ii] += encode_survey_source(0, mws_mask[name], 0)

    # FIXME (APC): expensive...
    assert(len(np.unique(encoded_targetid)) == len(encoded_targetid))
    return encoded_targetid


def encode_targetid(objid=None, brickid=None, release=None, mock=None, sky=None):
    """Create the DESI TARGETID from input source and imaging information

    Parameters
    ----------
    objid : :class:`int` or :class:`~numpy.ndarray`, optional
        The OBJID from Legacy Survey imaging (e.g. http://legacysurvey.org/dr4/catalogs/)
    brickid : :class:`int` or :class:`~numpy.ndarray`, optional
        The BRICKID from Legacy Survey imaging (e.g. http://legacysurvey.org/dr4/catalogs/)
    release : :class:`int` or :class:`~numpy.ndarray`, optional
        The RELEASE from Legacy Survey imaging (e.g. http://legacysurvey.org/dr4/catalogs/)
    mock : :class:`int` or :class:`~numpy.ndarray`, optional
        1 if this object is a mock object (generated from
        mocks, not from real survey data), 0 otherwise
    sky : :class:`int` or :class:`~numpy.ndarray`, optional
        1 if this object is a blank sky object, 0 otherwise

    Returns
    -------
    :class:`int` or `~numpy.ndarray`
        The TARGETID for DESI, encoded according to the bits listed in
        :meth:`desitarget.targetid_mask`. If an integer is passed, then an
        integer is returned, otherwise an array is returned

    Notes
    -----
        - This is set up with maximum flexibility so that mixes of integers
          and arrays can be passed, in case some value like BRICKID or SKY
          is the same for a set of objects. Consider, e.g.:

              print(
                  targets.decode_targetid(
                      targets.encode_targetid(objid=np.array([234,12]),
                                              brickid=np.array([234,12]),
                                              release=4,
                                              sky=[1,0]))
                                              )

        (array([234,12]), array([234,12]), array([4,4]), array([0,0]), array([1,0]))

        - See also https://desi.lbl.gov/DocDB/cgi-bin/private/RetrieveFile?docid=2348
    """
    # ADM a flag that tracks whether the main inputs were integers
    intpassed = True

    # ADM determine the length of whichever value was passed that wasn't None
    # ADM default to an integer (length 1)
    nobjs = 1
    inputs = [objid, brickid, release, sky, mock]
    goodpar = [input is not None for input in inputs]
    firstgoodpar = np.where(goodpar)[0][0]
    if isinstance(inputs[firstgoodpar], np.ndarray):
        nobjs = len(inputs[firstgoodpar])
        intpassed = False

    # ADM set parameters that weren't passed to zerod arrays
    # ADM set integers that were passed to at least 1D arrays
    if objid is None:
        objid = np.zeros(nobjs, dtype='int64')
    else:
        objid = np.atleast_1d(objid)
    if brickid is None:
        brickid = np.zeros(nobjs, dtype='int64')
    else:
        brickid = np.atleast_1d(brickid)
    if release is None:
        release = np.zeros(nobjs, dtype='int64')
    else:
        release = np.atleast_1d(release)
    if mock is None:
        mock = np.zeros(nobjs, dtype='int64')
    else:
        mock = np.atleast_1d(mock)
    if sky is None:
        sky = np.zeros(nobjs, dtype='int64')
    else:
        sky = np.atleast_1d(sky)

    # ADM check none of the passed parameters exceed their bit-allowance
    if not np.all(objid <= 2**targetid_mask.OBJID.nbits):
        log.error('Invalid range when creating targetid: OBJID cannot exceed {}'
                  .format(2**targetid_mask.OBJID.nbits))
    if not np.all(brickid <= 2**targetid_mask.BRICKID.nbits):
        log.error('Invalid range when creating targetid: BRICKID cannot exceed {}'
                  .format(2**targetid_mask.BRICKID.nbits))
    if not np.all(release <= 2**targetid_mask.RELEASE.nbits):
        log.error('Invalid range when creating targetid: RELEASE cannot exceed {}'
                  .format(2**targetid_mask.RELEASE.nbits))
    if not np.all(mock <= 2**targetid_mask.MOCK.nbits):
        log.error('Invalid range when creating targetid: MOCK cannot exceed {}'
                  .format(2**targetid_mask.MOCK.nbits))
    if not np.all(sky <= 2**targetid_mask.SKY.nbits):
        log.error('Invalid range when creating targetid: SKY cannot exceed {}'
                  .format(2**targetid_mask.SKY.nbits))

    # ADM set up targetid as an array of 64-bit integers
    targetid = np.zeros(nobjs, ('int64'))
    # ADM populate TARGETID based on the passed columns and desitarget.targetid_mask
    # ADM remember to shift to type integer 64 to avoid casting
    targetid |= objid.astype('int64') << targetid_mask.OBJID.bitnum
    targetid |= brickid.astype('int64') << targetid_mask.BRICKID.bitnum
    targetid |= release.astype('int64') << targetid_mask.RELEASE.bitnum
    targetid |= mock.astype('int64') << targetid_mask.MOCK.bitnum
    targetid |= sky.astype('int64') << targetid_mask.SKY.bitnum

    # ADM if the main inputs were integers, return an integer
    if intpassed:
        return targetid[0]
    return targetid


def decode_targetid(targetid):
    """break a DESI TARGETID into its constituent parts

    Parameters
    ----------
    :class:`int` or :class:`~numpy.ndarray`
        The TARGETID for DESI, encoded according to the bits listed in
        :meth:`desitarget.targetid_mask`

    Returns
    -------
    objid : :class:`int` or `~numpy.ndarray`
        The OBJID from Legacy Survey imaging (e.g. http://legacysurvey.org/dr4/catalogs/)
    brickid : :class:`int` or `~numpy.ndarray`
        The BRICKID from Legacy Survey imaging (e.g. http://legacysurvey.org/dr4/catalogs/)
    release : :class:`int` or `~numpy.ndarray`
        The RELEASE from Legacy Survey imaging (e.g. http://legacysurvey.org/dr4/catalogs/)
    mock : :class:`int` or `~numpy.ndarray`
        1 if this object is a mock object (generated from
        mocks, not from real survey data), 0 otherwise
    sky : :class:`int` or `~numpy.ndarray`
        1 if this object is a blank sky object, 0 otherwise

    Notes
    -----
        - if a 1-D array is passed, then an integer is returned. Otherwise an array
          is returned
        - see also https://desi.lbl.gov/DocDB/cgi-bin/private/RetrieveFile?docid=2348
    """

    # ADM retrieve each constituent value by left-shifting by the number of bits that comprise
    # ADM the value, to the left-end of the value, and then right-shifting to the right-end
    objid = (targetid & (2**targetid_mask.OBJID.nbits - 1
                         << targetid_mask.OBJID.bitnum)) >> targetid_mask.OBJID.bitnum
    brickid = (targetid & (2**targetid_mask.BRICKID.nbits - 1
                           << targetid_mask.BRICKID.bitnum)) >> targetid_mask.BRICKID.bitnum
    release = (targetid & (2**targetid_mask.RELEASE.nbits - 1
                           << targetid_mask.RELEASE.bitnum)) >> targetid_mask.RELEASE.bitnum
    mock = (targetid & (2**targetid_mask.MOCK.nbits - 1
                        << targetid_mask.MOCK.bitnum)) >> targetid_mask.MOCK.bitnum
    sky = (targetid & (2**targetid_mask.SKY.nbits - 1
                       << targetid_mask.SKY.bitnum)) >> targetid_mask.SKY.bitnum

    return objid, brickid, release, mock, sky


def main_cmx_or_sv(targets, rename=False, scnd=False):
    """determine whether a target array is main survey, commissioning, or SV

    Parameters
    ----------
    targets : :class:`~numpy.ndarray`
        An array of targets generated by, e.g., :mod:`~desitarget.cuts`
        must include at least (all of) the columns `DESI_TARGET`, `MWS_TARGET` and
        `BGS_TARGET` or the corresponding commissioning or SV columns.
    rename : :class:`bool`, optional, defaults to ``False``
        If ``True`` then also return a copy of `targets` with the input `_TARGET`
        columns renamed to reflect the main survey format.
    scnd : :class:`bool`, optional, defaults to ``False``
        If ``True``, then add the secondary target information to the output.

    Returns
    -------
    :class:`list`
        A list of strings corresponding to the target columns names. For the main survey
        this would be [`DESI_TARGET`, `BGS_TARGET`, `MWS_TARGET`], for commissioning it
        would just be [`CMX_TARGET`], for SV1 it would be [`SV1_DESI_TARGET`,
        `SV1_BGS_TARGET`, `SV1_MWS_TARGET`]. Also includes, e.g. `SCND_TARGET`, if
        `scnd` is passed as ``True``.
    :class:`list`
        A list of the masks that correspond to each column from the relevant main/cmx/sv
        yaml file. Also includes the relevant SCND_MASK, if `scnd` is passed as True.
    :class:`str`
        The string 'main', 'cmx' or 'svX' (where X = 1, 2, 3 etc.) for the main survey,
        commissioning and an iteration of SV. Specifies which type of file was sent.
    :class:`~numpy.ndarray`, optional, if `rename` is ``True``
        A copy of the input targets array with the `_TARGET` columns renamed to
        `DESI_TARGET`, and (if they exist) `BGS_TARGET`, `MWS_TARGET`.
    """
    # ADM default to the main survey.
    maincolnames = ["DESI_TARGET", "BGS_TARGET", "MWS_TARGET", "SCND_TARGET"]
    outcolnames = maincolnames.copy()
    masks = [desi_mask, bgs_mask, mws_mask, scnd_mask]
    survey = 'main'

    # ADM set survey to correspond to commissioning or SV if those columns exist
    # ADM and extract the column names of interest.
    incolnames = np.array(targets.dtype.names)
    notmain = np.array(['SV' in name or 'CMX' in name for name in incolnames])
    if np.any(notmain):
        outcolnames = list(incolnames[notmain])
        survey = outcolnames[0].split('_')[0].lower()
    if survey[0:2] == 'sv':
        outcolnames = ["{}_{}".format(survey.upper(), col) for col in maincolnames]

    # ADM retrieve the correct masks, depending on the survey type.
    if survey == 'cmx':
        from desitarget.cmx.cmx_targetmask import cmx_mask
        masks = [cmx_mask]
    elif survey[0:2] == 'sv':
        if survey == 'sv1':
            import desitarget.sv1.sv1_targetmask as targmask
        if survey == 'sv2':
            import desitarget.sv2.sv2_targetmask as targmask
        masks = [targmask.desi_mask, targmask.bgs_mask,
                 targmask.mws_mask, targmask.scnd_mask]
    elif survey != 'main':
        msg = "input target file must be 'main', 'cmx' or 'sv', not {}!!!".format(survey)
        log.critical(msg)
        raise ValueError(msg)

    if not scnd:
        outcolnames = outcolnames[:3]
        masks = masks[:3]

    # ADM if requested, rename the columns.
    if rename:
        mapper = {}
        for i, col in enumerate(outcolnames):
            mapper[col] = maincolnames[i]
        return outcolnames, masks, survey, rfn.rename_fields(targets, mapper)

    return outcolnames, masks, survey


def set_obsconditions(targets):
    """set the OBSCONDITIONS mask for each target bit.

    Parameters
    ----------
    targets : :class:`~numpy.ndarray`
        An array of targets generated by, e.g., :mod:`~desitarget.cuts`.
        Must include at least (all of) the columns `DESI_TARGET`,
        `BGS_TARGET`, `MWS_TARGET` or corresponding cmx or SV columns.

    Returns
    -------
    :class:`~numpy.ndarray`
        The OBSCONDITIONS bitmask for the passed targets.

    Notes
    -----
        - the OBSCONDITIONS for each target bit is in the file, e.g.
          data/targetmask.yaml. It can be retrieved using, for example,
          `obsconditions.mask(desi_mask["ELG"].obsconditions)`.
    """
    colnames, masks, _ = main_cmx_or_sv(targets)

    n = len(targets)
    obscon = np.zeros(n, dtype='i4')
    for mask, xxx_target in zip(masks, colnames):
        for name in mask.names():
            # ADM which targets have this bit for this mask set?
            ii = (targets[xxx_target] & mask[name]) != 0
            # ADM under what conditions can that bit be observed?
            if np.any(ii):
                obscon[ii] |= obsconditions.mask(mask[name].obsconditions)

    return obscon


def initial_priority_numobs(targets,
                            obscon="DARK|GRAY|BRIGHT|POOR|TWILIGHT12|TWILIGHT18"):
    """highest initial priority and numobs for an array of target bits.

    Parameters
    ----------
    targets : :class:`~numpy.ndarray`
        An array of targets generated by, e.g., :mod:`~desitarget.cuts`.
        Must include at least (all of) the columns `DESI_TARGET`,
        `BGS_TARGET`, `MWS_TARGET` or corresponding cmx or SV columns.
    obscon : :class:`str`, optional, defaults to almost all OBSCONDITIONS
        A combination of strings that are in the desitarget bitmask yaml
        file (specifically in `desitarget.targetmask.obsconditions`).

    Returns
    -------
    :class:`~numpy.ndarray`
        An array of integers corresponding to the highest initial
        priority for each target consistent with the constraints
        on observational conditions imposed by `obscon`.
    :class:`~numpy.ndarray`
        An array of integers corresponding to the largest number of
        observations for each target consistent with the constraints
        on observational conditions imposed by `obscon`.

    Notes
    -----
        - the initial priority for each target bit is in the file, e.g.,
          data/targetmask.yaml. It can be retrieved using, for example,
          `desi_mask["ELG"].priorities["UNOBS"]`.
        - the input obscon string can be converted to a bitmask using
          `desitarget.targetmask.obsconditions.mask(blat)`.
    """
    colnames, masks, _ = main_cmx_or_sv(targets)

    # ADM set up the output arrays.
    outpriority = np.zeros(len(targets), dtype='int')
    # ADM remember that calibs have NUMOBS of -1.
    outnumobs = np.zeros(len(targets), dtype='int')-1

    # ADM convert the passed obscon string to bits.
    obsbits = obsconditions.mask(obscon)

    # ADM loop through the masks to establish all bitnames of interest.
    for colname, mask in zip(colnames, masks):
        # ADM first determine which bits actually have priorities.
        bitnames = []
        for name in mask.names():
            try:
                _ = mask[name].priorities["UNOBS"]
                # ADM also only consider bits with correct OBSCONDITIONS.
                obsforname = obsconditions.mask(mask[name].obsconditions)
                if (obsforname & obsbits) != 0:
                    bitnames.append(name)
            except KeyError:
                pass

        # ADM loop through the relevant bits updating with the highest
        # ADM priority and the largest value of NUMOBS.
        for name in bitnames:
            # ADM indexes in the DESI/MWS/BGS_TARGET column that have this bit set
            istarget = (targets[colname] & mask[name]) != 0
            # ADM for each index, determine where this bit is set and the priority
            # ADM for this bit is > than the currently stored priority.
            w = np.where((mask[name].priorities['UNOBS'] >= outpriority) & istarget)[0]
            # ADM where a larger priority trumps the stored priority, update the priority
            if len(w) > 0:
                outpriority[w] = mask[name].priorities['UNOBS']
            # ADM for each index, determine where this bit is set and whether NUMOBS
            # ADM for this bit is > than the currently stored NUMOBS.
            w = np.where((mask[name].numobs >= outnumobs) & istarget)[0]
            # ADM where a larger NUMOBS trumps the stored NUMOBS, update NUMOBS.
            if len(w) > 0:
                outnumobs[w] = mask[name].numobs

    return outpriority, outnumobs


def encode_survey_source(survey, source, original_targetid):
    """
    """
    return (survey << SOURCE_END) + (source << USER_END) + original_targetid


def decode_survey_source(encoded_values):
    """
    Returns
    -------
        survey[:], source[:], original_targetid[:]
    """
    _encoded_values = np.asarray(np.atleast_1d(encoded_values), dtype=np.uint64)
    survey = (_encoded_values & ENCODE_MTL_SURVEY_MASK) >> SOURCE_END
    source = (_encoded_values & ENCODE_MTL_SOURCE_MASK) >> USER_END

    original_targetid = (encoded_values & ENCODE_MTL_USER_MASK)

    return survey, source, original_targetid


def calc_priority(targets, zcat):
    """
    Calculate target priorities from masks, observation/redshift status.

    Parameters
    ----------
    targets : :class:`~numpy.ndarray`
        numpy structured array or astropy Table of targets. Must include
        the columns `DESI_TARGET`, `BGS_TARGET`, `MWS_TARGET`
        (or their SV/cmx equivalents).
    zcat : :class:`~numpy.ndarray`
        numpy structured array or Table of redshift information. Must
        include 'Z', `ZWARN`, `NUMOBS` and be the same length as
        `targets`. May also contain `NUMOBS_MORE` if this isn't the
        first time through MTL and `NUMOBS > 0`.

    Returns
    -------
    :class:`~numpy.array`
        integer array of priorities.

    Notes
    -----
        - If a target passes multiple selections, highest priority wins.
        - Will automatically detect if the passed targets are main
          survey, commissioning or SV and behave accordingly.
    """
    # ADM check the input arrays are the same length.
    assert len(targets) == len(zcat)

    # ADM determine whether the input targets are main survey, cmx or SV.
    colnames, masks, survey = main_cmx_or_sv(targets)
    # ADM the target bits/names should be shared between main survey and SV.
    if survey != 'cmx':
        desi_target, bgs_target, mws_target = colnames
        desi_mask, bgs_mask, mws_mask = masks
    else:
        cmx_mask = masks[0]

    # Default is 0 priority, i.e. do not observe.
    priority = np.zeros(len(targets), dtype='i8')

    # Determine which targets have been observed.
    # TODO: this doesn't distinguish between really unobserved vs not yet
    # processed.
    unobs = (zcat["NUMOBS"] == 0)
    log.debug('calc_priority has %d unobserved targets' % (np.sum(unobs)))
    if np.all(unobs):
        done = np.zeros(len(targets), dtype=bool)
        zgood = np.zeros(len(targets), dtype=bool)
        zwarn = np.zeros(len(targets), dtype=bool)
    else:
        nmore = zcat["NUMOBS_MORE"]
        assert np.all(nmore >= 0)
        done = ~unobs & (nmore == 0)
        zgood = ~unobs & (nmore > 0) & (zcat['ZWARN'] == 0)
        zwarn = ~unobs & (nmore > 0) & (zcat['ZWARN'] != 0)

    # zgood, zwarn, done, and unobs should be mutually exclusive and cover all
    # targets.
    assert not np.any(unobs & zgood)
    assert not np.any(unobs & zwarn)
    assert not np.any(unobs & done)
    assert not np.any(zgood & zwarn)
    assert not np.any(zgood & done)
    assert not np.any(zwarn & done)
    assert np.all(unobs | done | zgood | zwarn)

    # DESI dark time targets.
    if survey != 'cmx':
        if desi_target in targets.dtype.names:
            # ADM 'LRG' is the guiding column in SV
            # ADM whereas 'LRG_1PASS' and 'LRG_2PASS' are in the main survey.
            names = ('ELG', 'LRG_1PASS', 'LRG_2PASS')
            if survey[0:2] == 'sv':
                names = ('ELG', 'LRG')
            for name in names:
                ii = (targets[desi_target] & desi_mask[name]) != 0
                priority[ii & unobs] = np.maximum(priority[ii & unobs], desi_mask[name].priorities['UNOBS'])
                priority[ii & done] = np.maximum(priority[ii & done],  desi_mask[name].priorities['DONE'])
                priority[ii & zgood] = np.maximum(priority[ii & zgood], desi_mask[name].priorities['MORE_ZGOOD'])
                priority[ii & zwarn] = np.maximum(priority[ii & zwarn], desi_mask[name].priorities['MORE_ZWARN'])

            # QSO could be Lyman-alpha or Tracer.
            name = 'QSO'
            ii = (targets[desi_target] & desi_mask[name]) != 0
            good_hiz = zgood & (zcat['Z'] >= 2.15) & (zcat['ZWARN'] == 0)
            priority[ii & unobs] = np.maximum(priority[ii & unobs], desi_mask[name].priorities['UNOBS'])
            priority[ii & done] = np.maximum(priority[ii & done], desi_mask[name].priorities['DONE'])
            priority[ii & good_hiz] = np.maximum(priority[ii & good_hiz], desi_mask[name].priorities['MORE_ZGOOD'])
            priority[ii & ~good_hiz] = np.maximum(priority[ii & ~good_hiz], desi_mask[name].priorities['DONE'])
            priority[ii & zwarn] = np.maximum(priority[ii & zwarn], desi_mask[name].priorities['MORE_ZWARN'])

        # BGS targets.
        if bgs_target in targets.dtype.names:
            for name in bgs_mask.names():
                ii = (targets[bgs_target] & bgs_mask[name]) != 0
                priority[ii & unobs] = np.maximum(priority[ii & unobs], bgs_mask[name].priorities['UNOBS'])
                priority[ii & done] = np.maximum(priority[ii & done],  bgs_mask[name].priorities['DONE'])
                priority[ii & zgood] = np.maximum(priority[ii & zgood], bgs_mask[name].priorities['MORE_ZGOOD'])
                priority[ii & zwarn] = np.maximum(priority[ii & zwarn], bgs_mask[name].priorities['MORE_ZWARN'])

        # MWS targets.
        if mws_target in targets.dtype.names:
            for name in mws_mask.names():
                ii = (targets[mws_target] & mws_mask[name]) != 0
                priority[ii & unobs] = np.maximum(priority[ii & unobs], mws_mask[name].priorities['UNOBS'])
                priority[ii & done] = np.maximum(priority[ii & done],  mws_mask[name].priorities['DONE'])
                priority[ii & zgood] = np.maximum(priority[ii & zgood], mws_mask[name].priorities['MORE_ZGOOD'])
                priority[ii & zwarn] = np.maximum(priority[ii & zwarn], mws_mask[name].priorities['MORE_ZWARN'])

        # Special case: IN_BRIGHT_OBJECT means priority=-1 no matter what
        ii = (targets[desi_target] & desi_mask.IN_BRIGHT_OBJECT) != 0
        priority[ii] = -1

    # ADM Special case: SV-like commissioning targets.
    if 'CMX_TARGET' in targets.dtype.names:
        for name in ['SV0_' + label for label in ('BGS', 'MWS')]:
            ii = (targets['CMX_TARGET'] & cmx_mask[name]) != 0
            priority[ii & unobs] = np.maximum(priority[ii & unobs], cmx_mask[name].priorities['UNOBS'])
            priority[ii & done] = np.maximum(priority[ii & done],  cmx_mask[name].priorities['DONE'])
            priority[ii & zgood] = np.maximum(priority[ii & zgood], cmx_mask[name].priorities['MORE_ZGOOD'])
            priority[ii & zwarn] = np.maximum(priority[ii & zwarn], cmx_mask[name].priorities['MORE_ZWARN'])

    return priority


def calc_numobs(targets):
    """
    Calculates the requested number of observations needed for each target

    Args:
        targets: numpy structured array or astropy Table of targets, including
            columns `DESI_TARGET`, `BGS_TARGET` or `MWS_TARGET`

    Returns:
        array of integers of requested number of observations

    Notes:
        This is `NUMOBS` desired before any spectroscopic observations; i.e.
            it does *not* take redshift into consideration (which is relevant
            for interpreting low-z vs high-z QSOs)
    """
    # Default is one observation
    nobs = np.ones(len(targets), dtype='i4')

    # If it wasn't selected by any target class, it gets 0 observations
    # Normally these would have already been removed, but just in case...
    no_target_class = np.ones(len(targets), dtype=bool)
    if 'DESI_TARGET' in targets.dtype.names:
        no_target_class &= targets['DESI_TARGET'] == 0
    if 'BGS_TARGET' in targets.dtype.names:
        no_target_class &= targets['BGS_TARGET'] == 0
    if 'MWS_TARGET' in targets.dtype.names:
        no_target_class &= targets['MWS_TARGET'] == 0

    n_no_target_class = np.sum(no_target_class)
    if n_no_target_class > 0:
        raise ValueError('WARNING: {:d} rows in targets.calc_numobs have no target class'.format(n_no_target_class))

    # - LRGs get 1, 2, or (perhaps) 3 observations depending upon magnitude
    # ADM set this using the LRG_1PASS/2PASS and maybe even 3PASS bits
    islrg = (targets['DESI_TARGET'] & desi_mask.LRG) != 0
    # ADM default to 2 passes for LRGs
    nobs[islrg] = 2
    # ADM for redundancy in case the defaults change, explicitly set
    # ADM NOBS for 1PASS and 2PASS LRGs
    try:
        lrg1 = (targets['DESI_TARGET'] & desi_mask.LRG_1PASS) != 0
        lrg2 = (targets['DESI_TARGET'] & desi_mask.LRG_2PASS) != 0
        nobs[lrg1] = 1
        nobs[lrg2] = 2
    except AttributeError:
        log.error('per-pass LRG bits not set in {}'.format(desi_mask))
    # ADM also reserve a setting for LRG_3PASS, but fail gracefully for now
    try:
        lrg3 = (targets['DESI_TARGET'] & desi_mask.LRG_3PASS) != 0
        nobs[lrg3] = 3
    except AttributeError:
        pass

    # - TBD: flag QSOs for 4 obs ahead of time, or only after confirming
    # - that they are redshift>2.15 (i.e. good for Lyman-alpha)?
    isqso = (targets['DESI_TARGET'] & desi_mask.QSO) != 0
    nobs[isqso] = 4

    # BGS: observe both BGS target classes once (and once only) on every epoch,
    # regardless of how many times it has been observed on previous epochs.

    # Priorities for MORE_ZWARN and MORE_ZGOOD are set in targetmask.yaml such
    # that targets are reobserved at the same priority until they have a good
    # redshift. Targets with good redshifts are still observed on subsequent
    # epochs but with a priority below all other BGS and MWS targets.

    if 'BGS_TARGET' in targets.dtype.names:
        # This forces the calculation of nmore in targets.calc_priority (and
        # ztargets['NOBS_MORE'] in mtl.make_mtl) to give nmore = 1 regardless
        # of targets['NUMOBS']
        ii = (targets['BGS_TARGET'] & bgs_mask.BGS_FAINT) != 0
        nobs[ii] = targets['NUMOBS'][ii]+1
        ii = (targets['BGS_TARGET'] & bgs_mask.BGS_BRIGHT) != 0
        nobs[ii] = targets['NUMOBS'][ii]+1
        ii = (targets['BGS_TARGET'] & bgs_mask.BGS_WISE) != 0
        nobs[ii] = targets['NUMOBS'][ii]+1

    return nobs


def resolve(targets):
    """Resolve which targets are primary in imaging overlap regions.

    Parameters
    ----------
    targets : :class:`~numpy.ndarray`
        Rec array of targets. Must have columns "RA" and "DEC" and
        either "RELEASE" or "PHOTSYS".

    Returns
    -------
    :class:`~numpy.ndarray`
        The original target list trimmed to only objects from the "northern"
        photometry in the northern imaging area and objects from "southern"
        photometry in the southern imaging area.
    """
    # ADM retrieve the photometric system from the RELEASE.
    from desitarget.io import release_to_photsys, desitarget_resolve_dec
    if 'PHOTSYS' in targets.dtype.names:
        photsys = targets["PHOTSYS"]
    else:
        photsys = release_to_photsys(targets["RELEASE"])

    # ADM a flag of which targets are from the 'N' photometry.
    from desitarget.cuts import _isonnorthphotsys
    photn = _isonnorthphotsys(photsys)

    # ADM grab the declination used to resolve targets.
    split = desitarget_resolve_dec()

    # ADM determine which targets are north of the Galactic plane. As
    # ADM a speed-up, bin in ~1 sq.deg. HEALPixels and determine
    # ADM which of those pixels are north of the Galactic plane.
    # ADM We should never be as close as ~1o to the plane.
    from desitarget.geomask import is_in_gal_box, pixarea2nside
    nside = pixarea2nside(1)
    theta, phi = np.radians(90-targets["DEC"]), np.radians(targets["RA"])
    pixnum = hp.ang2pix(nside, theta, phi, nest=True)
    # ADM find the pixels north of the Galactic plane...
    allpix = np.arange(hp.nside2npix(nside))
    theta, phi = hp.pix2ang(nside, allpix, nest=True)
    ra, dec = np.degrees(phi), 90-np.degrees(theta)
    pixn = is_in_gal_box([ra, dec], [0., 360., 0., 90.], radec=True)
    # ADM which targets are in pixels north of the Galactic plane.
    galn = pixn[pixnum]

    # ADM which targets are in the northern imaging area.
    arean = (targets["DEC"] >= split) & galn

    # ADM retain 'N' targets in 'N' area and 'S' in 'S' area.
    keep = (photn & arean) | (~photn & ~arean)

    return targets[keep]


def finalize(targets, desi_target, bgs_target, mws_target,
             sky=0, survey='main', darkbright=False):
    """Return new targets array with added/renamed columns

    Parameters
    ----------
    targets : :class:`~numpy.ndarray`
        numpy structured array of targets.
    desi_target : :class:`~numpy.ndarray`
        1D array of target selection bit flags.
    bgs_target : :class:`~numpy.ndarray`
        1D array of target selection bit flags.
    mws_target : :class:`~numpy.ndarray`
        1D array of target selection bit flags.
    sky : :class:`int`, defaults to 0
        Pass `1` to indicate these are blank sky targets, `0` otherwise.
    survey : :class:`str`, defaults to `main`
        Specifies which target masks yaml file to use. Options are `main`,
        `cmx` and `svX` (where X = 1, 2, 3 etc.) for the main survey,
        commissioning and an iteration of SV.
    darkbright : :class:`bool`, optional, defaults to ``False``
        If sent, then split `NUMOBS_INIT` and `PRIORITY_INIT` into
        `NUMOBS_INIT_DARK`, `NUMOBS_INIT_BRIGHT`, `PRIORITY_INIT_DARK`
        and `PRIORITY_INIT_BRIGHT` and calculate values appropriate
        to "BRIGHT" and "DARK|GRAY" observing conditions.

    Returns
    -------
    :class:`~numpy.ndarray`
       new targets structured array with the following additions:
          * renaming OBJID -> BRICK_OBJID (it is only unique within a brick).
          * renaming TYPE -> MORPHTYPE (used downstream in other contexts).
          * Adding new columns:
              - TARGETID: unique ID across all bricks.
              - DESI_TARGET: dark time survey target selection flags.
              - MWS_TARGET: bright time MWS target selection flags.
              - BGS_TARGET: bright time BGS target selection flags.
              - PRIORITY_INIT: initial priority for observing target.
              - SUBPRIORITY: a placeholder column that is set to zero.
              - NUMOBS_INIT: initial number of observations for target.
              - OBSCONDITIONS: bitmask of observation conditions.

    Notes
    -----
        - SUBPRIORITY is the only column that isn't populated. This is
          because it's easier to populate it in a reproducible fashion
          when collecting targets rather than on a per-brick basis
          when this function is called. It's set to all zeros.
    """
    ntargets = len(targets)
    assert ntargets == len(desi_target)
    assert ntargets == len(bgs_target)
    assert ntargets == len(mws_target)

    # - OBJID in tractor files is only unique within the brick; rename and
    # - create a new unique TARGETID
    targets = rfn.rename_fields(targets,
                                {'OBJID': 'BRICK_OBJID', 'TYPE': 'MORPHTYPE'})
    targetid = encode_targetid(objid=targets['BRICK_OBJID'],
                               brickid=targets['BRICKID'],
                               release=targets['RELEASE'],
                               sky=sky)

    nodata = np.zeros(ntargets, dtype='int')-1
    subpriority = np.zeros(ntargets, dtype='float')

    # ADM new columns are different depending on SV/cmx/main survey.
    if survey == 'main':
        colnames = ['DESI_TARGET', 'BGS_TARGET', 'MWS_TARGET']
    elif survey == 'cmx':
        colnames = ['CMX_TARGET']
    elif survey[0:2] == 'sv':
        colnames = ["{}_{}_TARGET".format(survey.upper(), tc)
                    for tc in ["DESI", "BGS", "MWS"]]
    else:
        msg = "survey must be 'main', 'cmx' or 'svX' (X=1,2..etc.), not {}!"   \
            .format(survey)
        log.critical(msg)
        raise ValueError(msg)

    # ADM the columns to write out and their values and formats.
    cols = ["TARGETID"] + colnames + ['SUBPRIORITY', 'OBSCONDITIONS']
    vals = [targetid] + [desi_target, bgs_target, mws_target][:len(colnames)]  \
        + [subpriority, nodata]
    forms = ['>i8'] + ['>i8', '>i8', '>i8'][:len(colnames)] + ['>f8', '>i8']

    # ADM set the initial PRIORITY and NUMOBS.
    if darkbright:
        # ADM populate bright/dark if splitting by survey OBSCONDITIONS.
        ender, obscon = ["_DARK", "_BRIGHT"], ["DARK|GRAY", "BRIGHT"]
    else:
        ender, obscon = [""], ["DARK|GRAY|BRIGHT|POOR|TWILIGHT12|TWILIGHT18"]
    for edr, oc in zip(ender, obscon):
        cols += ["{}_INIT{}".format(pn, edr) for pn in ["PRIORITY", "NUMOBS"]]
        vals += [nodata, nodata]
        forms += ['>i8', '>i8']

    # ADM write the output array.
    newdt = [dt for dt in zip(cols, forms)]
    done = np.array(np.zeros(len(targets)), dtype=targets.dtype.descr+newdt)
    for col in targets.dtype.names:
        done[col] = targets[col]
    for col, val in zip(cols, vals):
        done[col] = val

    # ADM add PRIORITY/NUMOBS columns.
    for edr, oc in zip(ender, obscon):
        pc, nc = "PRIORITY_INIT"+edr, "NUMOBS_INIT"+edr
        done[pc], done[nc] = initial_priority_numobs(done, obscon=oc)

    # ADM set the OBSCONDITIONS.
    done["OBSCONDITIONS"] = set_obsconditions(done)

    # ADM some final checks that the targets conform to expectations...
    # ADM check that each target has a unique ID.
    if len(done["TARGETID"]) != len(set(done["TARGETID"])):
        msg = 'TARGETIDs are not unique!'
        log.critical(msg)
        raise AssertionError(msg)

    # ADM check all LRG targets have LRG_1PASS/2PASS set.
    if survey == 'main':
        lrgset = done["DESI_TARGET"] & desi_mask.LRG != 0
        pass1lrgset = done["DESI_TARGET"] & desi_mask.LRG_1PASS != 0
        pass2lrgset = done["DESI_TARGET"] & desi_mask.LRG_2PASS != 0
        if not np.all(lrgset == pass1lrgset | pass2lrgset):
            msg = 'Some LRG targets do not have 1PASS/2PASS set!'
            log.critical(msg)
            raise AssertionError(msg)

    return done
