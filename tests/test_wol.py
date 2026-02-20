"""Tests for Wake-on-LAN functionality."""

from unittest.mock import patch

from hozo.core.wol import wake


class TestWake:
    """Tests for wake function."""

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_sends_magic_packet(self, mock_send: object) -> None:
        """Should call send_magic_packet with correct MAC."""
        mac = "AA:BB:CC:DD:EE:FF"

        result = wake(mac)

        assert result is True
        mock_send.assert_called_once_with(  # type: ignore[attr-defined]
            mac, ip_address="255.255.255.255", port=9
        )

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_custom_broadcast(self, mock_send: object) -> None:
        """Should use custom broadcast IP when provided."""
        mac = "AA:BB:CC:DD:EE:FF"
        broadcast = "192.168.1.255"

        wake(mac, ip_address=broadcast)

        mock_send.assert_called_once_with(  # type: ignore[attr-defined]
            mac, ip_address=broadcast, port=9
        )

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_custom_port(self, mock_send: object) -> None:
        """Should use custom port when provided."""
        mac = "AA:BB:CC:DD:EE:FF"

        wake(mac, port=7)

        mock_send.assert_called_once_with(  # type: ignore[attr-defined]
            mac, ip_address="255.255.255.255", port=7
        )

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_returns_true_on_success(self, mock_send: object) -> None:
        """Should always return True on successful send."""
        result = wake("11:22:33:44:55:66")
        assert result is True
