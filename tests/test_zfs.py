"""
Tests for backupd.zfs — ZFS pool inspection on the remote backup machine.

All ``subprocess.run`` calls are mocked; no real zpool commands run.
"""

import subprocess
from unittest.mock import MagicMock, patch

from backupd.zfs import (
    _parse_pool_status,
    export_pool,
    get_pool_status,
    list_pools,
)

_ZPOOL_STATUS_ONE = """\
  pool: tank
 state: ONLINE
  scan: scrub repaired 0B in 00:01:23 with 0 errors
config:
        NAME        STATE     READ WRITE CKSUM
        tank        ONLINE       0     0     0
"""

_ZPOOL_STATUS_TWO = """\
  pool: tank
 state: ONLINE
config:
        tank        ONLINE

  pool: backup
 state: DEGRADED
config:
        backup      DEGRADED
"""


class TestGetPoolStatus:
    @patch("subprocess.run")
    def test_returns_parsed_states(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=_ZPOOL_STATUS_ONE)
        assert get_pool_status() == {"tank": "ONLINE"}

    @patch("subprocess.run")
    def test_appends_pool_name_to_cmd(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=_ZPOOL_STATUS_ONE)
        get_pool_status(pool="tank")
        assert mock_run.call_args[0][0] == ["zpool", "status", "tank"]

    @patch("subprocess.run")
    def test_no_pool_omits_name(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")
        get_pool_status()
        assert mock_run.call_args[0][0] == ["zpool", "status"]

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_empty_when_zpool_missing(self, _: MagicMock) -> None:
        assert get_pool_status() == {}

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("zpool", 15))
    def test_returns_empty_on_timeout(self, _: MagicMock) -> None:
        assert get_pool_status() == {}


class TestParsePoolStatus:
    def test_single_pool(self) -> None:
        assert _parse_pool_status(_ZPOOL_STATUS_ONE) == {"tank": "ONLINE"}

    def test_multiple_pools(self) -> None:
        assert _parse_pool_status(_ZPOOL_STATUS_TWO) == {
            "tank": "ONLINE",
            "backup": "DEGRADED",
        }

    def test_state_before_pool_is_ignored(self) -> None:
        # A 'state:' line with no preceding 'pool:' must not create an entry.
        assert _parse_pool_status(" state: ONLINE\n") == {}

    def test_empty_output(self) -> None:
        assert _parse_pool_status("") == {}


class TestListPools:
    @patch("subprocess.run")
    def test_returns_pool_names(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="tank\nbackup\n")
        assert list_pools() == ["tank", "backup"]

    @patch("subprocess.run")
    def test_uses_expected_argv(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="tank\n")
        list_pools()
        assert mock_run.call_args[0][0] == ["zpool", "list", "-H", "-o", "name"]

    @patch("subprocess.run")
    def test_strips_blank_lines(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="tank\n\n  \nbackup\n")
        assert list_pools() == ["tank", "backup"]

    @patch("subprocess.run", side_effect=Exception("boom"))
    def test_returns_empty_on_exception(self, _: MagicMock) -> None:
        assert list_pools() == []


class TestExportPool:
    @patch("subprocess.run")
    def test_returns_true_on_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        assert export_pool("tank") is True
        assert mock_run.call_args[0][0] == ["zpool", "export", "tank"]

    @patch("subprocess.run")
    def test_returns_false_on_nonzero(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="pool is busy")
        assert export_pool("tank") is False

    @patch("subprocess.run", side_effect=Exception("kaboom"))
    def test_returns_false_on_exception(self, _: MagicMock) -> None:
        assert export_pool("tank") is False
