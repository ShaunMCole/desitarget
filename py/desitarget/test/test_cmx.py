# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-
"""Test desitarget.cmx.
"""
import unittest
from pkg_resources import resource_filename
import os.path
from uuid import uuid4
import numbers

from astropy.io import fits
from astropy.table import Table
import fitsio
import numpy as np

from desitarget import io
from desitarget.cmx import cmx_cuts as cuts


class TestCMX(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.datadir = resource_filename('desitarget.test', 't')
        cls.tractorfiles = sorted(io.list_tractorfiles(cls.datadir))
        cls.sweepfiles = sorted(io.list_sweepfiles(cls.datadir))
        cls.cmxdir = resource_filename('desitarget.test', 't3')

    def test_cuts_basic(self):
        """Test cuts work with either data or filenames
        """
        # ADM test for tractor files.
        # ADM No QSO cuts for speed. This doesn't affect coverage.
        cmx, pshift = cuts.apply_cuts(self.tractorfiles[0],
                                      cmxdir=self.cmxdir, noqso=True)
        data = io.read_tractor(self.tractorfiles[0])
        cmx2, pshift2 = cuts.apply_cuts(data,
                                        cmxdir=self.cmxdir, noqso=True)
        self.assertTrue(np.all(cmx == cmx2))
        self.assertTrue(np.all(pshift == pshift2))

        # ADM test for sweeps files.
        # ADM No QSO cuts for speed. This doesn't affect coverage.
        cmx, pshift = cuts.apply_cuts(self.sweepfiles[0],
                                      cmxdir=self.cmxdir, noqso=True)
        data = io.read_tractor(self.sweepfiles[0])
        cmx2, pshift2 = cuts.apply_cuts(data,
                                        cmxdir=self.cmxdir, noqso=True)
        self.assertTrue(np.all(cmx == cmx2))
        self.assertTrue(np.all(pshift == pshift2))

    def _test_table_row(self, targets):
        """Test cuts work with tables from several I/O libraries
        """
        # ADM add the DR7/DR8 data columns if they aren't there yet.
        # ADM can remove this once DR8 is finalized.
        if "MASKBITS" not in targets.dtype.names:
            targets = io.add_dr8_columns(targets)

        cmx, pshift = cuts.apply_cuts(targets,
                                      cmxdir=self.cmxdir)
        self.assertEqual(len(cmx), len(targets))

        cmx, pshift = cuts.apply_cuts(targets[0],
                                      cmxdir=self.cmxdir)
        self.assertTrue(isinstance(cmx, numbers.Integral), 'CMX_TARGET mask not an int')

    def test_astropy_fits(self):
        """Test astropy.fits I/O library
        """
        targets = fits.getdata(self.tractorfiles[0])
        self._test_table_row(targets)

    def test_astropy_table(self):
        """Test astropy tables I/O library
        """
        targets = Table.read(self.tractorfiles[0])
        self._test_table_row(targets)

    def test_numpy_ndarray(self):
        """Test fitsio I/O library
        """
        targets = fitsio.read(self.tractorfiles[0], upper=True)
        self._test_table_row(targets)

    def test_select_targets(self):
        """Test select targets works with either data or filenames
        """
        for filelist in [self.tractorfiles, self.sweepfiles]:
            # ADM No QSO cuts for speed. This doesn't affect coverage.
            targets = cuts.select_targets(filelist, numproc=1,
                                          cmxdir=self.cmxdir, noqso=True)
            t1 = cuts.select_targets(filelist[0:1], numproc=1,
                                     cmxdir=self.cmxdir, noqso=True)
            t2 = cuts.select_targets(filelist[0], numproc=1,
                                     cmxdir=self.cmxdir, noqso=True)
            for col in t1.dtype.names:
                try:
                    notNaN = ~np.isnan(t1[col])
                except TypeError:  # - can't check string columns for NaN
                    notNaN = np.ones(len(t1), dtype=bool)

                self.assertTrue(np.all(t1[col][notNaN] == t2[col][notNaN]))

    def test_missing_files(self):
        """Test the code will die gracefully if input files are missing
        """
        with self.assertRaises(ValueError):
            targets = cuts.select_targets(['blat.foo1234', ], numproc=1)

    def test_parallel_select(self):
        """Test multiprocessing parallelization works
        """
        for nproc in [1, 2]:
            for filelist in [self.tractorfiles, self.sweepfiles]:
                # ADM No QSO cuts for speed. Doesn't affect coverage.
                targets = cuts.select_targets(filelist, numproc=nproc,
                                              cmxdir=self.cmxdir, noqso=True)
                self.assertTrue('CMX_TARGET' in targets.dtype.names)
                self.assertEqual(len(targets), np.count_nonzero(targets['CMX_TARGET']))


if __name__ == '__main__':
    unittest.main()


def test_suite():
    """Allows testing of only this module with the command:

        python setup.py test -m desitarget.test.test_cmx
    """
    return unittest.defaultTestLoader.loadTestsFromName(__name__)
