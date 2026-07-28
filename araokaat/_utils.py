#!/usr/bin/env python3
#
#  utils.py
"""
General helpers.
"""
#
#  Copyright © 2026 Dominic Davis-Foster <dominic@davis-foster.co.uk>
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
import re
import sys
from abc import abstractmethod
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional, Protocol, TextIO, Tuple, Union
from unicodedata import east_asian_width
from weakref import proxy

CUR_OS = sys.platform
IS_WIN = any(CUR_OS.startswith(i) for i in ["win32", "cygwin"])
IS_NIX = any(CUR_OS.startswith(i) for i in ["aix", "linux", "darwin", "freebsd"])
RE_ANSI = re.compile(r"\x1b\[[;\d]*[A-Za-z]")

colorama: Optional[ModuleType]
try:
	if IS_WIN:
		# 3rd party
		import colorama
	else:
		raise ImportError
except ImportError:
	colorama = None
else:
	if colorama:
		try:
			colorama.init(strip=False)
		except TypeError:
			colorama.init()

if TYPE_CHECKING:
	# stdlib
	from multiprocessing.synchronize import RLock as _MP_RLock
	from threading import RLock as _T_RLock
	RLock = Union[_MP_RLock, _T_RLock]

	# this package
	import araokaat

__all__ = [
		"DefaultWriteLock",
		"DisableOnWriteError",
		"EMA",
		"FormatReplace",
		"SupportsFormat",
		"disp_len",
		"disp_trim",
		]


class FormatReplace:
	"""

	:param replace:

	.. code-block:: python

		>>> a = FormatReplace("something")
		>>> f"{a:5d}"
		'something'

	"""

	def __init__(self, replace: str = ''):
		self.replace = replace
		self.format_called = 0

	def __format__(self, _) -> str:
		self.format_called += 1
		return self.replace


class DisableOnWriteError:
	"""
	Disable the given araokaat instance upon ``write()`` or ``flush()`` errors.

	:param wrapped:
	:param instance:
	"""

	@staticmethod
	def disable_on_exception(instance: "araokaat.araokaat", func: Callable[..., Any]) -> Callable[..., Any]:
		"""
		Quietly set ``instance.miniters=inf`` if ``func`` raises ``errno=5``.

		:param instance:
		:param func:
		"""

		instance = proxy(instance)

		def inner(*args, **kwargs) -> Any:
			try:
				return func(*args, **kwargs)
			except OSError as e:
				if e.errno != 5:
					raise
				try:
					instance.miniters = float("inf")
				except ReferenceError:
					pass
			except ValueError as e:
				if "closed" not in str(e):
					raise
				try:
					instance.miniters = float("inf")
				except ReferenceError:
					pass

		return inner

	def __init__(self, wrapped: TextIO, instance: "araokaat.araokaat"):
		self.wrapper_setattr("_wrapped", wrapped)
		if hasattr(wrapped, "write"):
			self.wrapper_setattr(
					"write",
					self.disable_on_exception(instance, wrapped.write),
					)
		if hasattr(wrapped, "flush"):
			self.wrapper_setattr(
					"flush",
					self.disable_on_exception(instance, wrapped.flush),
					)

	def __getattr__(self, name: str) -> Any:
		return getattr(self._wrapped, name)

	def __setattr__(self, name: str, value: Any) -> None:
		setattr(self._wrapped, name, value)

	def wrapper_getattr(self, name: str) -> Any:
		# Actual ``self.getattr`` rather than ``self._wrapped.getattr``.

		return getattr(self, name)

	def wrapper_setattr(self, name: str, value: Any) -> None:
		# Actual ``self.setattr`` rather than self._wrapped.setattr.

		object.__setattr__(self, name, value)

	def __eq__(self, other: Any) -> bool:
		return self._wrapped == getattr(other, "_wrapped", other)


def _is_utf(encoding: str) -> bool:
	try:
		"█▉".encode(encoding)
	except UnicodeEncodeError:
		return False
	except Exception:
		try:
			return encoding.lower().startswith("utf-") or ("U8" == encoding)
		except Exception:
			return False
	else:
		return True


def _supports_unicode(fp: TextIO) -> bool:
	try:
		return _is_utf(fp.encoding)
	except AttributeError:
		return False


def _is_ascii(s: Union[str, TextIO]) -> bool:
	if isinstance(s, str):
		for c in s:
			if ord(c) > 255:
				return False

		return True

	return _supports_unicode(s)


_ScreenSize = Tuple[Optional[int], Optional[int]]


def _screen_shape_wrapper() -> Callable[[TextIO], _ScreenSize]:  # pragma: no cover
	"""
	Return a function which returns console dimensions ``(width, height)``.
	"""

	# stdlib
	from os import get_terminal_size

	def inner(fp: TextIO) -> _ScreenSize:
		if not hasattr(fp, "fileno"):
			return None, None
		try:
			cols, lines = get_terminal_size(fp.fileno())
			return cols - 1, lines - 1
		except Exception:
			return None, None

	return inner


def disp_len(data: str) -> int:
	"""
	Returns the real on-screen length of a string which may contain ANSI control codes and wide chars.

	:param data:
	"""

	s = RE_ANSI.sub('', data)
	return sum(2 if east_asian_width(ch) in "FW" else 1 for ch in str(s))


def disp_trim(data: str, length: int) -> str:
	"""
	Trim a string which may contain ANSI control characters.

	:param data:
	:param length:
	"""

	if len(data) == disp_len(data):
		return data[:length]

	ansi_present = bool(RE_ANSI.search(data))

	while disp_len(data) > length:  # carefully delete one char at a time
		data = data[:-1]

	if ansi_present and bool(RE_ANSI.search(data)):
		# assume ANSI reset is required
		return data if data.endswith("\u001b[0m") else data + "\u001b[0m"

	return data


def _trlock(*args, **kwargs) -> Optional["RLock"]:
	# Threading RLock.

	try:
		# stdlib
		from threading import RLock
		return RLock(*args, **kwargs)
	except (ImportError, OSError):  # pragma: no cover
		return None


class DefaultWriteLock:
	"""
	Provide a default write lock for thread and multiprocessing safety.

	Works only on platforms supporting `fork` (so Windows is excluded).

	You must initialise a `araokaat` or `DefaultWriteLock` instance
	before forking in order for the write lock to work.

	On Windows, you need to supply the lock from the parent to the children as
	an argument to joblib or the parallelism lib you use.
	"""

	# global thread lock so no setup required for multithreading.
	# NB: Do not create multiprocessing lock as it sets the multiprocessing
	# context, disallowing `spawn()`/`forkserver()`
	th_lock = _trlock()

	mp_lock: ClassVar[Optional["RLock"]]

	def __init__(self) -> None:
		# Create global parallelism locks to avoid racing issues with parallel
		# bars works only if fork available (Linux/MacOSX, but not Windows)
		cls = type(self)
		root_lock = cls.th_lock
		if root_lock is not None:
			root_lock.acquire()
		cls.create_mp_lock()
		self.locks = [lk for lk in [cls.mp_lock, cls.th_lock] if lk is not None]
		if root_lock is not None:
			root_lock.release()

	def acquire(self, *a, **k) -> None:
		for lock in self.locks:
			lock.acquire(*a, **k)

	def release(self) -> None:
		for lock in self.locks[::-1]:  # Release in inverse order of acquisition
			lock.release()

	def __enter__(self) -> None:
		self.acquire()

	def __exit__(self, *exc) -> None:
		self.release()

	@classmethod
	def create_mp_lock(cls) -> None:
		if not hasattr(cls, "mp_lock"):
			try:
				# stdlib
				from multiprocessing import RLock
				cls.mp_lock = RLock()
			except (ImportError, OSError):  # pragma: no cover
				cls.mp_lock = None


class EMA:
	"""
	Exponential moving average: smoothing to give progressively lower weights to older values.

	:param smoothing: Smoothing factor.
		Increase to give more weight to recent values.
		Ranges from ``0`` (yields old value) to ``1`` (yields new value).
	"""

	def __init__(self, smoothing: float = 0.3):
		self.alpha = smoothing
		self.last: float = 0
		self.calls = 0

	def __call__(self, x: Optional[float] = None) -> float:
		"""

		:param x: New value to include in EMA.
		"""

		beta = 1 - self.alpha

		if x is not None:
			self.last = self.alpha * x + beta * self.last
			self.calls += 1

		return self.last / (1 - beta**self.calls) if self.calls else self.last


class SupportsFormat(Protocol):
	__slots__ = ()

	@abstractmethod
	def __format__(self, __s: str) -> str:
		pass
