#!/usr/bin/env python3
#
#  __init__.py
"""
Customisable progress bar decorator for iterators.
Includes a default `range` iterator printing to `stderr`.

Usage:
>>> from araokaat import araokaat
>>> for i in araokaat(range(10)):
...	 ...
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

__author__: str = "Dominic Davis-Foster"
__copyright__: str = "2026 Dominic Davis-Foster"
__license__: str = "MIT License"
__version__: str = "0.1.0b1"
__email__: str = "dominic@davis-foster.co.uk"

# stdlib
import os
import sys
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from numbers import Number
from time import time
from typing import (
		Any,
		Callable,
		ClassVar,
		Dict,
		Generic,
		Iterable,
		Iterator,
		Mapping,
		Optional,
		Set,
		TextIO,
		Tuple,
		Type,
		TypeVar,
		Union,
		overload
		)
from warnings import warn
from weakref import WeakSet

# 3rd party
from typing_extensions import Self

# this package
from araokaat._monitor import TMonitor
from araokaat._utils import (
		EMA,
		DefaultWriteLock,
		DisableOnWriteError,
		FormatReplace,
		SupportsFormat,
		_is_ascii,
		_screen_shape_wrapper,
		_ScreenSize,
		_supports_unicode,
		colorama,
		disp_len,
		disp_trim
		)

__all__ = ["Bar", "MonitorWarning", "araokaat", "format_interval", "format_meter", "format_num", "format_sizeof"]


class MonitorWarning(RuntimeWarning):
	"""
	Warning for errors with the monitor thread which do not affect external functionality.
	"""


class Bar:
	"""
	``str.format``-able bar with format specifiers: ``[width][type]``.

	- ``width``
	  + unspecified (default): use ``self.default_len``
	  + `int >= 0`: overrides ``self.default_len``
	  + `int < 0`: subtract from ``self.default_len``
	- ``type``
	  + ``a``: ascii (``charset=self.ASCII`` override)
	  + ``u``: unicode (``charset=self.UTF`` override)
	  + ``b``: blank (``charset="  "`` override)

	:param frac:
	:param default_len:
	:param charset:
	:param colour:
	"""

	ASCII = " 123456789#"
	UTF = ' ' + ''.join(map(chr, range(0x258F, 0x2587, -1)))
	BLANK = "  "
	COLOUR_RESET = "\u001b[0m"
	COLOUR_RGB = "\u001b[38;2;%d;%d;%dm"
	COLOURS = {
			"BLACK": "\u001b[30m",
			"RED": "\u001b[31m",
			"GREEN": "\u001b[32m",
			"YELLOW": "\u001b[33m",
			"BLUE": "\u001b[34m",
			"MAGENTA": "\u001b[35m",
			"CYAN": "\u001b[36m",
			"WHITE": "\u001b[37m",
			}

	frac: float
	default_len: int
	charset: str
	_colour: Optional[str]

	def __init__(self, frac: float, default_len: int = 10, charset: str = UTF, colour: Optional[str] = None):
		if not 0 <= frac <= 1:
			warn("clamping frac to range [0, 1]", stacklevel=2)
			frac = max(0, min(1, frac))

		assert default_len > 0
		self.frac = frac
		self.default_len = default_len
		self.charset = charset
		self.colour = colour

	@property
	def colour(self) -> Optional[str]:
		"""
		The bar colour.
		"""

		return self._colour

	@colour.setter
	def colour(self, value: Optional[str]) -> None:
		if not value:
			self._colour = None
			return
		try:
			if value.upper() in self.COLOURS:
				self._colour = self.COLOURS[value.upper()]
			elif value[0] == '#' and len(value) == 7:
				self._colour = self.COLOUR_RGB % tuple(int(i, 16) for i in (value[1:3], value[3:5], value[5:7]))
			else:
				raise KeyError
		except (KeyError, AttributeError):
			warn(
					f"Unknown colour ({value}); valid choices:"
					f" [hex (#00ff00), {', '.join(self.COLOURS)}]",
					stacklevel=2,
					)
			self._colour = None

	def __format__(self, format_spec: str) -> str:
		if format_spec:
			_type = format_spec[-1].lower()
			try:
				charset = {'a': self.ASCII, 'u': self.UTF, 'b': self.BLANK}[_type]
			except KeyError:
				charset = self.charset
			else:
				format_spec = format_spec[:-1]
			if format_spec:
				N_BARS = int(format_spec)
				if N_BARS < 0:
					N_BARS += self.default_len
			else:
				N_BARS = self.default_len
		else:
			charset = self.charset
			N_BARS = self.default_len

		nsyms = len(charset) - 1
		bar_length, frac_bar_length = divmod(int(self.frac * N_BARS * nsyms), nsyms)

		res = charset[-1] * bar_length
		if bar_length < N_BARS:  # whitespace padding
			res = res + charset[frac_bar_length] + charset[0] * (N_BARS - bar_length - 1)
		return self.colour + res + self.COLOUR_RESET if self.colour else res


_T = TypeVar("_T")


class araokaat(Generic[_T]):
	"""
	Create a progressbar for an iterable.

	Wraps an iterable, returning an iterator which acts exactly like the original iterable
	but prints a dynamically updating progress bar every time a value is requested.

	:param iterable: Iterable to decorate with a progress bar.
		Leave blank to manually manage the updates.
	:param desc: Prefix for the progress bar.
	:param total: The number of expected iterations.
		If unspecified, ``len(iterable)`` is used if possible.
		If ``float("inf")`` or as a last resort, only basic progress statistics are displayed (no ETA, no progress bar).
	:param leave: If :py:obj:`True`, keeps all traces of the progress bar upon termination of iteration.
		If :py:obj:`None`, will leave only if ``position`` is ``0``.
	:param file: Specifies where to output the progress messages.
		Uses ``file.write(str)`` and ``file.flush()`` methods.
	:param ncols: The width of the entire output message.
		If specified, dynamically resizes the progress bar to stay within this bound.
		If unspecified, attempts to use environment width.
		The fallback is a meter width of ``10`` and no limit for the counter and statistics.
		If ``0``, will not print any meter (only stats).
	:param mininterval: Minimum progress display update interval in seconds.
	:param maxinterval: Maximum progress display update interval in seconds.
		Automatically adjusts `miniters` to correspond to ``mininterval`` after long display update lag.
		Only works if ``dynamic_miniters`` or monitor thread is enabled.
	:param miniters: Minimum progress display update interval, in iterations.
		If ``0`` and ``dynamic_miniters``, will automatically adjust to equal ``mininterval`` (more CPU efficient, good for tight loops).
		If ``> 0`` will skip display of specified number of iterations.
		Tweak this and ``mininterval`` to get very efficient loops.
		If your progress is erratic with both fast and slow iterations (network, skipping items, etc.) you should set ``miniters=1``.
	:param ascii: If unspecified or :py:obj:`False`, use unicode (smooth blocks) to fill the meter.
		The fallback is to use ASCII characters `` 123456789#``.
	:param disable: Whether to disable the entire progress bar wrapper.
		If :py:obj:`None`, disable on non-TTY.
	:param unit: String that will be used to define the unit of each iteration
	:param unit_scale: If ``1`` or :py:obj:`True`,
		the number of iterations will be reduced/scaled automatically and an SI prefix will be added (kilo, mega, etc.).
		If any other non-zero number will scale ``total`` and ``n``.
	:param dynamic_ncols: If set, constantly alters ``ncols`` and ``nrows`` to the environment (allowing for window resizes).
	:param smoothing: Exponential moving average smoothing factor for speed estimates.
		Ranges from ``0`` (average speed) to ``1`` (current/instantaneous speed).
	:param bar_format: Specify a custom bar string formatting. May impact performance.
		[default: '{l_bar}{bar}{r_bar}'], where
		l_bar='{desc}: {percentage:3.0f}%|' and
		r_bar='| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]'
		Possible vars: ``l_bar``, ``bar``, ``r_bar``, ``n``, ``n_fmt``, ``total``, ``total_fmt``,
		``percentage``, ``elapsed``, ``elapsed_s``, ``ncols``, ``nrows``, ``desc``, ``unit``,
		``rate``, ``rate_fmt``, ``rate_noinv``, ``rate_noinv_fmt``,
		``rate_inv``, ``rate_inv_fmt``, ``postfix``, ``unit_divisor``,
		``remaining``, ``remaining_s``, ``eta``.
		Note that a trailing ": " is automatically removed after ``{desc}`` if the latter is empty.
	:param initial: The initial counter value. Useful when restarting a progress bar.
		If using float, consider specifying ``{n:.3f}`` or similar in ``bar_format``,
		or specifying ``unit_scale``.
	:param position: Specify the line offset to print this bar (starting from ``0``).
		Automatic if unspecified.
		Useful to manage multiple bars at once (e.g. from threads).
	:param postfix: Specify additional stats to display at the end of the bar.
	:param unit_divisor: Ignored unless ``unit_scale`` is :py:obj:`True`.
	:param lock_args: Passed to `refresh` for intermediate output (initialisation, iterating, and updating).
	:param nrows: The screen height. If specified, hides nested bars outside this bound.
		If unspecified, attempts to use environment height. The fallback is ``20``.
	:param colour: Bar colour (e.g. ``'green'``, ``'#00ff00'``).
	:param delay: Don't display until this many seconds have elapsed.

	Returns
	-------
	out  : decorated iterator.
	"""

	monitor_interval = 10  # set to 0 to disable the thread
	monitor = None
	_instances: Set["araokaat"] = WeakSet()  # type: ignore[assignment]
	_lock: ClassVar[DefaultWriteLock]
	disable: bool
	_ema_dt: Callable[..., Optional[float]]

	@overload
	def __init__(
			self: "araokaat[None]",
			iterable: None = None,
			desc: str = '',
			total: Optional[float] = None,
			leave: bool = True,
			file: Optional[TextIO] = None,
			ncols: Optional[int] = None,
			mininterval: float = 0.1,
			maxinterval: float = 10.0,
			miniters: Optional[float] = None,
			ascii: Union[bool, str, None] = None,  # noqa: A002  # pylint: disable=redefined-builtin
			disable: Optional[bool] = False,
			unit: str = "it",
			unit_scale: Union[bool, float] = False,
			dynamic_ncols: bool = False,
			smoothing: float = 0.3,
			bar_format: Optional[str] = None,
			initial: float = 0,
			position: Optional[int] = None,
			postfix: Union[str, Mapping[str, Any], None] = None,
			unit_divisor: float = 1000,
			lock_args: Union[Tuple[Optional[bool], Optional[float]], Tuple[Optional[bool]], None] = None,
			nrows: Optional[int] = None,
			colour: Optional[str] = None,
			delay: float = 0.0,
			): ...

	@overload
	def __init__(
			self: "araokaat[_T]",
			iterable: Iterable[_T],
			desc: str = '',
			total: Optional[float] = None,
			leave: bool = True,
			file: Optional[TextIO] = None,
			ncols: Optional[int] = None,
			mininterval: float = 0.1,
			maxinterval: float = 10.0,
			miniters: Optional[float] = None,
			ascii: Union[bool, str, None] = None,  # noqa: A002  # pylint: disable=redefined-builtin
			disable: Optional[bool] = False,
			unit: str = "it",
			unit_scale: Union[bool, float] = False,
			dynamic_ncols: bool = False,
			smoothing: float = 0.3,
			bar_format: Optional[str] = None,
			initial: float = 0,
			position: Optional[int] = None,
			postfix: Union[str, Mapping[str, Any], None] = None,
			unit_divisor: float = 1000,
			lock_args: Union[Tuple[Optional[bool], Optional[float]], Tuple[Optional[bool]], None] = None,
			nrows: Optional[int] = None,
			colour: Optional[str] = None,
			delay: float = 0.0,
			): ...

	def __init__(
			self,
			iterable: Optional[Iterable[_T]] = None,
			desc: str = '',
			total: Optional[float] = None,
			leave: bool = True,
			file: Optional[TextIO] = None,
			ncols: Optional[int] = None,
			mininterval: float = 0.1,
			maxinterval: float = 10.0,
			miniters: Optional[float] = None,
			ascii: Union[bool, str, None] = None,  # noqa: A002  # pylint: disable=redefined-builtin
			disable: Optional[bool] = False,
			unit: str = "it",
			unit_scale: Union[bool, float] = False,
			dynamic_ncols: bool = False,
			smoothing: float = 0.3,
			bar_format: Optional[str] = None,
			initial: float = 0,
			position: Optional[int] = None,
			postfix: Union[str, Mapping[str, Any], None] = None,
			unit_divisor: float = 1000,
			lock_args: Union[Tuple[Optional[bool], Optional[float]], Tuple[Optional[bool]], None] = None,
			nrows: Optional[int] = None,
			colour: Optional[str] = None,
			delay: float = 0.0,
			):

		if file is None:
			file = sys.stderr

		file = DisableOnWriteError(file, instance=self)  # type: ignore[assignment]

		if disable is None:
			disable = (hasattr(file, "isatty") and not file.isatty())

		if total is None and iterable is not None:
			try:
				total = len(iterable)  # type: ignore[arg-type]
			except (TypeError, AttributeError):
				total = None
		if total == float("inf"):
			# Infinite iterations, behave same as unknown
			total = None

		if disable:
			self.iterable = iterable
			self.disable = disable
			with self._lock:
				self.pos = self._get_free_pos(self)
				self._instances.remove(self)
			self.n = initial
			self.total = total
			self.leave = leave
			return

		# Preprocess the arguments
		dynamic_ncols_fn: Optional[Callable[[TextIO], _ScreenSize]] = None
		_auto_ncols = ((ncols is None or nrows is None) and (file in (sys.stderr, sys.stdout)))
		if _auto_ncols or dynamic_ncols:  # pragma: no cover
			if dynamic_ncols:
				dynamic_ncols_fn = _screen_shape_wrapper()
				ncols, nrows = dynamic_ncols_fn(file)
			else:
				_dynamic_ncols = _screen_shape_wrapper()
				_ncols, _nrows = _dynamic_ncols(file)
				if ncols is None:
					ncols = _ncols
				if nrows is None:
					nrows = _nrows

		if miniters is None:
			miniters = 0
			dynamic_miniters = True
		else:
			dynamic_miniters = False

		if ascii is None:
			ascii = not _supports_unicode(file)  # noqa: A001  # pylint: disable=redefined-builtin

		if bar_format:
			if not ascii or (isinstance(ascii, str) and not _is_ascii(ascii)):
				# Convert bar format into unicode since terminal uses unicode
				bar_format = str(bar_format)

		# Store the arguments
		self.iterable = iterable
		self.desc = desc
		self.total = total
		self.leave = leave
		self.fp: TextIO = file
		self.ncols = ncols
		self.nrows = nrows
		self.mininterval = mininterval
		self.maxinterval = maxinterval
		self.miniters = miniters
		self.dynamic_miniters = dynamic_miniters
		self.ascii: Union[str, bool] = ascii
		self.disable = disable
		self.unit = unit
		self.unit_scale = unit_scale
		self.unit_divisor = unit_divisor
		self.initial = initial
		self.lock_args = lock_args
		self.delay = delay
		self.dynamic_ncols = dynamic_ncols_fn
		self.smoothing = smoothing
		self._ema_dn = EMA(smoothing)
		self._ema_dt = EMA(smoothing)
		self._ema_miniters = EMA(smoothing)
		self.bar_format = bar_format
		self.colour = colour
		self._time = time

		if postfix is None:
			self.postfix = None
		elif isinstance(postfix, Mapping):
			self.set_postfix(refresh=False, **postfix)
		else:
			self.postfix = postfix

		# Init the iterations counters
		self.last_print_n = initial
		self.n = initial

		# if nested, at initial sp() call we replace '\r' by '\n' to
		# not overwrite the outer progress bar
		with self._lock:
			# mark fixed positions as negative
			self.pos = self._get_free_pos(self) if position is None else -position

		# Initialize the screen printer
		self.sp = self.status_printer(self.fp)
		if delay <= 0:
			self.refresh(lock_args=self.lock_args)

		# Init the time counter
		self.last_print_t = self._time()
		# NB: Avoid race conditions by setting start_t at the very end of init
		self.start_t = self.last_print_t

	@staticmethod
	def status_printer(file: TextIO) -> Callable[[str], None]:
		"""
		Manage the printing and in-place updating of a line of characters.
		Note that if the string is longer than a line, then in-place
		updating may not work (it will print a new line at each refresh).

		:param file:
		"""

		fp_flush = getattr(file, "flush", lambda: None)  # pragma: no cover
		if file in (sys.stderr, sys.stdout):
			getattr(sys.stderr, "flush", lambda: None)()
			getattr(sys.stdout, "flush", lambda: None)()

		def fp_write(s: str) -> None:
			file.write(str(s))
			fp_flush()

		last_len = [0]

		def print_status(s: str) -> None:
			len_s = disp_len(s)
			fp_write('\r' + s + (' ' * max(last_len[0] - len_s, 0)))
			last_len[0] = len_s

		return print_status

	def __new__(cls: Type[Self], *_, **__) -> Self:  # noqa: D102
		instance = object.__new__(cls)

		with cls.get_lock():  # also constructs lock if non-existent
			cls._instances.add(instance)
			# create monitoring thread
			if cls.monitor_interval and (cls.monitor is None or not cls.monitor.report()):
				try:
					cls.monitor = TMonitor(cls, cls.monitor_interval)
				except Exception as e:  # pragma: nocover
					warn(
							"araokaat:disabling monitor support"
							" (monitor_interval = 0) due to:\n" + str(e),
							MonitorWarning,
							stacklevel=2,
							)
					cls.monitor_interval = 0

		return instance

	@classmethod
	def _get_free_pos(cls, instance: Optional["araokaat"] = None) -> int:
		# Skips specified instance.

		positions = {abs(inst.pos) for inst in cls._instances if inst is not instance and hasattr(inst, "pos")}
		return min(set(range(len(positions) + 1)).difference(positions))

	@classmethod
	def _decr_instances(cls, instance: "araokaat") -> None:
		# Remove from list and reposition another unfixed bar to fill the new gap.
		# This means that by default (where all nested bars are unfixed),
		# order is not maintained but screen flicker/blank space is minimised.

		with cls._lock:
			try:
				cls._instances.remove(instance)
			except KeyError:
				pass

			last = (instance.nrows or 20) - 1
			# find unfixed (`pos >= 0`) overflow (`pos >= nrows - 1`)
			instances = list(filter(
					lambda i: hasattr(i, "pos") and last <= i.pos,
					cls._instances,
					))

			# set first found to current `pos`
			if instances:
				inst = min(instances, key=lambda i: i.pos)
				inst.clear(nolock=True)
				inst.pos = abs(instance.pos)

	def write(self, s: str, file: TextIO = sys.stdout, end: str = '\n', nolock: bool = False) -> None:
		"""
		Print a message without overlapping the progressbar.

		:param s: Text to print.
		:param file:
		:param end:
		:param nolock: Don't acquire the global lock.
		"""

		with self.external_write_mode(file=file, nolock=nolock):
			# Write the message
			file.write(s)
			file.write(end)

	@classmethod
	@contextmanager
	def external_write_mode(cls, file: TextIO = sys.stdout, nolock: bool = False) -> Iterator[None]:
		"""
		Disable araokaat within context and refresh araokaat when exits.

		Useful when writing to standard output stream

		:param file:
		:param nolock: Don't acquire the global lock.
		"""

		try:
			if not nolock:
				cls.get_lock().acquire()
			# Clear all bars
			inst_cleared = []

			for inst in getattr(cls, "_instances", []):
				# Clear instance if in the target output file
				# or if write output + araokaat output are both either
				# sys.stdout or sys.stderr (because both are mixed in terminal)
				if hasattr(
						inst,
						"start_t",
						) and (inst.fp == file or all(f in (sys.stdout, sys.stderr) for f in (file, inst.fp))):
					inst.clear(nolock=True)
					inst_cleared.append(inst)
			yield

			# Force refresh display of bars we cleared
			for inst in inst_cleared:
				inst.refresh(nolock=True)
		finally:
			if not nolock:
				cls._lock.release()

	@classmethod
	def set_lock(cls, lock: DefaultWriteLock) -> None:
		"""
		Set the global lock.

		:param lock:
		"""

		cls._lock = lock

	@classmethod
	def get_lock(cls) -> DefaultWriteLock:
		"""
		Get the global lock. Construct it if it does not exist.
		"""

		if not hasattr(cls, "_lock"):
			cls._lock = DefaultWriteLock()

		return cls._lock

	def __bool__(self) -> bool:
		if self.total is not None:
			return self.total > 0
		if self.iterable is None:
			raise TypeError("bool() undefined when iterable == total == None")
		return bool(self.iterable)

	def __lt__(self, other: Any) -> bool:
		return self._comparable < other._comparable

	def __le__(self, other: Any) -> bool:
		return (self < other) or (self == other)

	def __eq__(self, other: Any) -> bool:
		return self._comparable == other._comparable

	def __ne__(self, other: Any) -> bool:
		return not self == other

	def __gt__(self, other: Any) -> bool:
		return not self <= other

	def __ge__(self, other: Any) -> bool:
		return not self < other

	def __len__(self) -> int:
		if self.iterable is None:
			return self.total  # type: ignore[return-value]
		elif hasattr(self.iterable, "shape"):
			return self.iterable.shape[0]
		elif hasattr(self.iterable, "__len__"):
			return len(self.iterable)  # type: ignore[arg-type]
		elif hasattr(self.iterable, "__length_hint__"):
			return self.iterable.__length_hint__()
		else:
			return getattr(self, "total", None)  # type: ignore[return-value]

	def __reversed__(self) -> Iterator[_T]:
		if self.iterable is None:
			raise TypeError("'araokaat' object is not reversible")

		try:
			orig = self.iterable
		except AttributeError:
			raise TypeError("'araokaat' object is not reversible")
		else:
			self.iterable = reversed(self.iterable)  # type: ignore[call-overload]
			return self.__iter__()
		finally:
			self.iterable = orig

	def __contains__(self, item: Any) -> bool:
		contains = getattr(self.iterable, "__contains__", None)
		return (
				contains(item) if contains is not None  # pylint: disable=not-callable
				else item in self.__iter__()
				)

	def __enter__(self: Self) -> Self:
		return self

	def __exit__(self, exc_type, exc_value, traceback) -> None:
		try:
			self.close()
		except AttributeError:
			# maybe eager thread cleanup upon external error
			if (exc_type, exc_value, traceback) == (None, None, None):
				raise
			warn("AttributeError ignored", stacklevel=2)

	def __del__(self) -> None:
		self.close()

	def __str__(self) -> str:
		return format_meter(**self.format_dict)

	@property
	def _comparable(self) -> bool:
		return abs(getattr(self, "pos", 1 << 31))

	def __hash__(self) -> int:
		return id(self)

	def __iter__(self) -> Iterator[_T]:
		# Inlining instance variables as locals (speed optimisation)
		iterable = self.iterable

		if iterable is None:
			raise TypeError("'araokaat' object is not iterable")

		# If the bar is disabled, then just walk the iterable
		# (note: keep this check outside the loop for performance)
		if self.disable:
			for obj in iterable:
				yield obj
			return

		mininterval = self.mininterval
		last_print_t = self.last_print_t
		last_print_n = self.last_print_n
		min_start_t = self.start_t + self.delay
		n = self.n
		time = self._time

		try:
			for obj in iterable:
				yield obj
				# Update and possibly print the progress bar.
				# Note: does not call self.update(1) for speed optimisation.
				n += 1

				if n - last_print_n >= self.miniters:
					cur_t = time()
					dt = cur_t - last_print_t
					if dt >= mininterval and cur_t >= min_start_t:
						self.update(n - last_print_n)
						last_print_n = self.last_print_n
						last_print_t = self.last_print_t
		finally:
			self.n = n
			self.close()

	def update(self, n: float = 1) -> None:
		"""
		Manually update the progress bar, useful for streams such as reading files.

		Example:

		.. code-block:: python3

			>>> t = araokaat(total=filesize) # Initialise
			>>> for current_buffer in stream:
			...	...
			...	t.update(len(current_buffer))
			>>> t.close()

		The last line is highly recommended, but possibly not necessary if ``update()``
		will be called in such a way that ``filesize`` will be exactly reached and printed.

		:param n: Increment to add to the internal counter of iterations.
			If a float, consider specifying ``{n:.3f}`` or similar in ``bar_format``,
			or specifying ``unit_scale``.
		"""

		if self.disable:
			return

		if n < 0:
			self.last_print_n += n  # for auto-refresh logic to work
		self.n += n

		# check counter first to reduce calls to time()
		if self.n - self.last_print_n >= self.miniters:
			cur_t = self._time()
			dt = cur_t - self.last_print_t
			if dt >= self.mininterval and cur_t >= self.start_t + self.delay:
				cur_t = self._time()
				dn = self.n - self.last_print_n  # >= n
				if self.smoothing and dt and dn:
					# EMA (not just overall average)
					self._ema_dn(dn)
					self._ema_dt(dt)
				self.refresh(lock_args=self.lock_args)
				if self.dynamic_miniters:
					# If no `miniters` was specified, adjust automatically to the
					# maximum iteration rate seen so far between two prints.
					# e.g.: After running `araokaat.update(5)`, subsequent
					# calls to `araokaat.update()` will only cause an update after
					# at least 5 more iterations.
					if self.maxinterval and dt >= self.maxinterval:
						self.miniters = dn * (self.mininterval or self.maxinterval) / dt
					elif self.smoothing:
						# EMA miniters update
						self.miniters = self._ema_miniters(
								dn * (self.mininterval / dt if self.mininterval and dt else 1),
								)
					else:
						# max iters between two prints
						self.miniters = max(self.miniters, dn)

				# Store old values for next call
				self.last_print_n = self.n
				self.last_print_t = cur_t

	def close(self) -> None:
		"""
		Cleanup and (if ``leave=False``) close the progress bar.
		"""

		if self.disable:
			return

		# Prevent multiple closures
		self.disable = True

		# decrement instance pos and remove from internal set
		pos = abs(self.pos)
		self._decr_instances(self)

		if self.last_print_t < self.start_t + self.delay:
			# haven't ever displayed; nothing to clear
			return

		# annoyingly, _supports_unicode isn't good enough
		def fp_write(s: str) -> None:
			self.fp.write(str(s))

		try:
			fp_write('')
		except ValueError as e:
			if "closed" in str(e):
				return
			raise  # pragma: no cover

		leave = pos == 0 if self.leave is None else self.leave

		with self._lock:
			if leave:
				# stats for overall rate (no weighted average)
				self._ema_dt = lambda *args: None
				self.display(pos=0)
				fp_write('\n')
			else:
				# clear previous display
				if self.display(msg='', pos=pos) and not pos:
					fp_write('\r')

	def clear(self, nolock: bool = False) -> None:
		"""
		Clear current bar display.

		:param nolock: Don't acquire the global lock.
		"""

		if self.disable:
			return

		if not nolock:
			self._lock.acquire()

		pos = abs(self.pos)

		if pos < (self.nrows or 20):
			self.moveto(pos)
			self.sp('')
			self.fp.write('\r')  # place cursor back at the beginning of line
			self.moveto(-pos)

		if not nolock:
			self._lock.release()

	def refresh(
			self,
			nolock: bool = False,
			lock_args: Union[Tuple[Optional[bool], Optional[float]], Tuple[Optional[bool]], None] = None,
			) -> None:
		"""
		Force refresh the display of this bar.

		:param nolock: If :py:obj:`True`, does not lock.
			If :py:obj:`False` calls ``acquire()`` on internal lock.
		:param lock_args: Passed to internal lock's ``acquire()``.
			If specified, will only ``display()`` if ``acquire()`` returns :py:obj:`True`.
		"""

		if self.disable:
			return

		if not nolock:
			if lock_args:
				self._lock.acquire(*lock_args)
				return
			else:
				self._lock.acquire()

		self.display()

		if not nolock:
			self._lock.release()

		return

	def unpause(self) -> None:
		"""
		Restart timer from last print time.
		"""

		if self.disable:
			return

		cur_t = self._time()
		self.start_t += cur_t - self.last_print_t
		self.last_print_t = cur_t

	def reset(self, total: Optional[float] = None) -> None:
		"""
		Resets to ``0`` iterations for repeated use.

		Consider combining with ``leave=True``.

		:param total: Total to use for the new bar.
		"""

		self.n = 0

		if total is not None:
			self.total = total

		if self.disable:
			return

		self.last_print_n = 0
		self.last_print_t = self.start_t = self._time()
		self._ema_dn = EMA(self.smoothing)
		self._ema_dt = EMA(self.smoothing)
		self._ema_miniters = EMA(self.smoothing)
		self.refresh()

	def set_description(self, desc: str = '', refresh: bool = True) -> None:
		"""
		Set/modify description of the progress bar.

		:param desc:
		:param refresh: Force a refresh.
		"""

		self.desc = desc + ": " if desc else ''

		if refresh:
			self.refresh()

	def set_description_str(self, desc: str = '', refresh: bool = True) -> None:
		"""
		Set/modify description without ``': '`` appended.

		:param desc:
		:param refresh: Force a refresh.
		"""

		self.desc = desc or ''

		if refresh:
			self.refresh()

	def set_postfix(
			self,
			ordered_dict: Union[Dict, OrderedDict, None] = None,
			refresh: bool = True,
			**kwargs,
			) -> None:
		r"""
		Set/modify postfix (additional stats) with automatic formatting based on datatype.

		:param ordered_dict:
		:param refresh: Force a refresh.
		:param \*\*kwargs:
		"""

		# Sort in alphabetical order to be more deterministic
		postfix = OrderedDict([] if ordered_dict is None else ordered_dict)

		for key in sorted(kwargs.keys()):
			postfix[key] = kwargs[key]

		# Preprocess stats according to datatype
		for key in postfix.keys():
			# Number: limit the length of the string
			if isinstance(postfix[key], Number):
				postfix[key] = format_num(postfix[key])
			# Else for any other type, try to get the string conversion
			elif not isinstance(postfix[key], str):
				postfix[key] = str(postfix[key])
			# Else if it's a string, don't need to preprocess anything

		# Stitch together to get the final postfix
		self.postfix = ", ".join(key + '=' + postfix[key].strip() for key in postfix.keys())

		if refresh:
			self.refresh()

	def set_postfix_str(self, s: str = '', refresh: bool = True) -> None:
		"""
		Postfix without dictionary expansion, similar to prefix handling.

		:param s:
		:param refresh: Force a refresh.
		"""

		self.postfix = str(s)
		if refresh:
			self.refresh()

	def moveto(self, n: int) -> None:  # noqa: D102  # TODO
		move_up = '' if (os.name == "nt") and (colorama is None) else "\u001b[A"
		self.fp.write('\n' * n + move_up * -n)
		getattr(self.fp, "flush", lambda: None)()

	@property
	def format_dict(self) -> Dict[str, Any]:  # noqa: D102  # TODO
		if self.disable and not hasattr(self, "unit"):
			return defaultdict(
					lambda: None,
					{
							'n': self.n,
							"total": self.total,
							"elapsed": 0,
							"unit": "it",
							},
					)
		if self.dynamic_ncols:
			self.ncols, self.nrows = self.dynamic_ncols(self.fp)

		rate: Optional[float] = None
		_ema_dt = self._ema_dt()
		if _ema_dt:
			rate = self._ema_dn() / _ema_dt

		return {
				'n': self.n,
				"total": self.total,
				"elapsed": self._time() - self.start_t if hasattr(self, "start_t") else 0,
				"ncols": self.ncols,
				"nrows": self.nrows,
				"prefix": self.desc,
				"ascii": self.ascii,
				"unit": self.unit,
				"unit_scale": self.unit_scale,
				"rate": rate,
				"bar_format": self.bar_format,
				"postfix": self.postfix,
				"unit_divisor": self.unit_divisor,
				"initial": self.initial,
				"colour": self.colour,
				}

	def display(self, msg: Optional[str] = None, pos: Optional[int] = None) -> bool:
		"""
		Use ``self.sp`` to display ``msg`` in the specified ``pos``.

		Consider overloading this function when inheriting to use e.g.:
		``self.some_frontend(**self.format_dict)`` instead of ``self.sp``.

		:param msg:What to display (default: ``repr(self)``).
		:param pos: Position to ``moveto`` (default: ``abs(self.pos)``).
		:no-default: pos
		"""

		if pos is None:
			pos = abs(self.pos)

		nrows = self.nrows or 20
		if pos >= nrows - 1:
			if pos >= nrows:
				return False
			if msg or msg is None:  # override at `nrows - 1`
				msg = " ... (more hidden) ..."

		if pos:
			self.moveto(pos)

		self.sp(self.__str__() if msg is None else msg)

		if pos:
			self.moveto(-pos)

		return True


def format_meter(
		n: float,
		total: Optional[float],
		elapsed: float,
		ncols: Optional[int] = None,
		prefix: Optional[str] = '',
		ascii: Union[bool, str, None] = False,  # noqa: A002  # pylint: disable=redefined-builtin
		unit: str = "it",
		unit_scale: Union[bool, float, None] = False,
		rate: Optional[float] = None,
		bar_format: Optional[str] = None,
		postfix: Optional[str] = None,
		unit_divisor: float = 1000,
		initial: float = 0,
		colour: Optional[str] = None,
		**kwargs,
		) -> str:
	r"""
	Return a string-based progress bar given some parameters.

	:param n: Number of finished iterations.
	:param total: The expected total number of iterations.
		If :py:obj:`None` only basic progress statistics are displayed (no ETA).
	:param elapsed: Number of seconds passed since start.
	:param ncols: The width of the entire output message.
		If specified, dynamically resizes ``{bar}`` to stay within this bound.
		If ``0``, will not print any bar (only stats). The fallback is ``{bar:10}``.
	:param prefix: Prefix message (included in total width). Use as ``{desc}`` in ``bar_format`` string.
	:param ascii: If not set, use unicode (smooth blocks) to fill the meter.
		The fallback is to use ASCII characters `` 123456789#``.
	:param unit: The iteration unit.
	:param unit_scale: If ``1`` or :py:obj:`True`, the number of iterations will be printed with an
		appropriate SI metric prefix (k = 10^3, M = 10^6, etc.).
		If any other non-zero number, will scale ``total`` and ``n``.
	:param rate: Manual override for iteration rate.
		If :py:obj:`None`, uses ``n/elapsed``.
	:param bar_format: Specify a custom bar string formatting. May impact performance.
		[default: ``'{l_bar}{bar}{r_bar}'``], where
		``l_bar='{desc}: {percentage:3.0f}%|'`` and
		``r_bar='| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]'``
		Possible vars: ``l_bar``, ``bar``, ``r_bar``, ``n``, ``n_fmt``, ``total``, ``total_fmt``,
		``percentage``, ``elapsed``, ``elapsed_s``, ``ncols``, ``nrows``, ``desc``, ``unit``,
		``rate``, ``rate_fmt``, ``rate_noinv``, ``rate_noinv_fmt``,
		``rate_inv``, ``rate_inv_fmt``, ``postfix``, ``unit_divisor``,
		``remaining``, ``remaining_s``, ``eta``.
		Note that a trailing ``": "`` is automatically removed after ``{desc}`` if the latter is empty.
	:param postfix: Similar to ``prefix``, but placed at the end (e.g. for additional stats).
		Postfix is usually a string (not a dict) for this method, and will if possible be set to ``postfix = ', ' + postfix``.
		However other types are supported.
	:param unit_divisor: Ignored unless ``unit_scale`` is :py:obj:`True`.
	:param initial: The initial counter value.
	:param colour: Bar colour (e.g. ``'green'``, ``'#00ff00'``).
	:param \*\*kwargs:

	:returns: Formatted meter and stats, ready to display.
	"""

	# sanity check: total
	if total and n >= (total + 0.5):  # allow float imprecision (#849)
		total = None

	# apply custom scale if necessary
	if unit_scale and unit_scale not in (True, 1):
		if total:
			total *= unit_scale
		n *= unit_scale
		if rate:
			rate *= unit_scale  # by default rate = self.avg_dn / self.avg_dt
		unit_scale = False

	elapsed_str = format_interval(elapsed)

	# if unspecified, attempt to use rate = average speed
	# (we allow manual override since predicting time is an arcane art)
	if rate is None and elapsed:
		rate = (n - initial) / elapsed
	inv_rate = 1 / rate if rate else None

	_format_rate = lambda r: (format_sizeof(r) if unit_scale else f'{r:5.2f}')
	rate_noinv_fmt = (_format_rate(rate) if rate else '?') + unit + "/s"
	rate_inv_fmt = (_format_rate(inv_rate) if inv_rate else '?') + "s/" + unit
	rate_fmt = rate_inv_fmt if inv_rate and inv_rate > 1 else rate_noinv_fmt

	if unit_scale:
		n_fmt = format_sizeof(n, divisor=unit_divisor)
		total_fmt = format_sizeof(total, divisor=unit_divisor) if total is not None else '?'
	else:
		n_fmt = str(n)
		total_fmt = str(total) if total is not None else '?'

	try:
		postfix = ", " + postfix if postfix else ''
	except TypeError:
		pass

	remaining = (total - n) / rate if rate and total else 0
	remaining_str = format_interval(remaining) if rate else '?'
	try:
		eta_dt = (
				datetime.now()
				+ timedelta(seconds=remaining) if rate and total else datetime.fromtimestamp(0, timezone.utc)
				)
	except OverflowError:
		eta_dt = datetime.max

	# format the stats displayed to the left and right sides of the bar
	if prefix:
		# old prefix setup work around
		bool_prefix_colon_already = (prefix[-2:] == ": ")
		l_bar = prefix if bool_prefix_colon_already else prefix + ": "
	else:
		l_bar = ''

	r_bar = f'| {n_fmt}/{total_fmt} [{elapsed_str}<{remaining_str}, {rate_fmt}{postfix}]'

	# Custom bar formatting
	# Populate a dict with all available progress indicators
	format_dict = {
			# slight extension of self.format_dict
			'n': n,
			"n_fmt": n_fmt,
			"total": total,
			"total_fmt": total_fmt,
			"elapsed": elapsed_str,
			"elapsed_s": elapsed,
			"ncols": ncols,
			"desc": prefix or '',
			"unit": unit,
			"rate": inv_rate if inv_rate and inv_rate > 1 else rate,
			"rate_fmt": rate_fmt,
			"rate_noinv": rate,
			"rate_noinv_fmt": rate_noinv_fmt,
			"rate_inv": inv_rate,
			"rate_inv_fmt": rate_inv_fmt,
			"postfix": postfix,
			"unit_divisor": unit_divisor,
			"colour": colour,  # plus more useful definitions
			"remaining": remaining_str,
			"remaining_s": remaining,
			"l_bar": l_bar,
			"r_bar": r_bar,
			"eta": eta_dt,
			**kwargs,
			}

	full_bar: SupportsFormat

	# total is known: we can predict some stats
	if total:
		# fractional and percentage progress
		frac = n / total
		percentage = frac * 100

		l_bar += f'{percentage:3.0f}%|'

		if ncols == 0:
			return l_bar[:-1] + r_bar[1:]

		format_dict.update(l_bar=l_bar)
		if bar_format:
			format_dict.update(percentage=percentage)

			# auto-remove colon for empty `{desc}`
			if not prefix:
				bar_format = bar_format.replace("{desc}: ", '')
		else:
			bar_format = "{l_bar}{bar}{r_bar}"

		full_bar = FormatReplace()
		nobar = bar_format.format(bar=full_bar, **format_dict)
		if not full_bar.format_called:
			return nobar  # no `{bar}`; nothing else to do

		# Formatting progress bar space available for bar's display
		full_bar = Bar(
				frac,
				max(1, ncols - disp_len(nobar)) if ncols else 10,
				charset=Bar.ASCII if ascii is True else ascii or Bar.UTF,
				colour=colour,
				)
		if not _is_ascii(full_bar.charset) and _is_ascii(bar_format):
			bar_format = str(bar_format)
		res = bar_format.format(bar=full_bar, **format_dict)
		return disp_trim(res, ncols) if ncols else res

	elif bar_format:
		# user-specified bar_format but no total
		l_bar += '|'
		format_dict.update(l_bar=l_bar, percentage=0)
		full_bar = FormatReplace()
		nobar = bar_format.format(bar=full_bar, **format_dict)
		if not full_bar.format_called:
			return nobar
		full_bar = Bar(
				0,
				max(1, ncols - disp_len(nobar)) if ncols else 10,
				charset=Bar.BLANK,
				colour=colour,
				)
		res = bar_format.format(bar=full_bar, **format_dict)
		return disp_trim(res, ncols) if ncols else res

	else:
		# no total: no bar & ETA, just progress stats
		return (f'{(prefix + ": ") if prefix else ""}{n_fmt}{unit} [{elapsed_str}, {rate_fmt}{postfix}]')


def format_sizeof(num: float, suffix: str = '', divisor: float = 1000) -> str:
	"""
	Formats a number with SI prefix.

	:param num: Number (``>= 1``) to format.
	:param suffix: Post-postfix.
	:param divisor: Divisor between prefixes.
	"""

	for unit in ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z']:
		if abs(num) < 999.5:
			if abs(num) < 99.95:
				if abs(num) < 9.995:
					return f'{num:1.2f}{unit}{suffix}'
				return f'{num:2.1f}{unit}{suffix}'
			return f'{num:3.0f}{unit}{suffix}'
		num /= divisor
	return f'{num:3.1f}Y{suffix}'


def format_interval(t: float) -> str:
	"""
	Formats a number of seconds as a clock time ``[H:]MM:SS``.

	:param t: Number of seconds.

	:returns: ``[H:]MM:SS``
	"""

	sign = '-' if t < 0 else ''
	mins, s = divmod(abs(int(t)), 60)
	h, m = divmod(mins, 60)
	return f'{sign}{h:d}:{m:02d}:{s:02d}' if h else f'{sign}{m:02d}:{s:02d}'


def format_num(n: float) -> str:
	"""
	Intelligent scientific notation (.3g).

	:param n: A Number.

	:returns: Formatted number.
	"""

	f = f'{n:.3g}'.replace("e+0", "e+").replace("e-0", "e-")
	n_str = str(n)
	return f if len(f) < len(n_str) else n_str
