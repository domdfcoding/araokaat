"""
Shared pytest config.
"""

#
#  Based on `tqdm`
#  https://github.com/tqdm/tqdm/
#
#  This Source Code Form is subject to the terms of the
#  Mozilla Public License, v. 2.0.
#  If a copy of the MPL was not distributed with this project,
#  You can obtain one at https://mozilla.org/MPL/2.0/.
#

# stdlib
import sys
from typing import Iterator

# 3rd party
import pytest

# this package
from araokaat import araokaat


@pytest.fixture(autouse=True)
def pretest_posttest() -> Iterator[None]:
	"""Fixture for all tests ensuring environment cleanup"""
	sys.setswitchinterval(1)

	if getattr(araokaat, "_instances", False):
		n = len(araokaat._instances)
		if n:
			araokaat._instances.clear()
			raise OSError(f"{n} `araokaat` instances still in existence PRE-test")
	yield
	if getattr(araokaat, "_instances", False):
		n = len(araokaat._instances)
		if n:
			araokaat._instances.clear()
			raise OSError(f"{n} `araokaat` instances still in existence POST-test")
