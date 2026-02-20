"""Wake-on-LAN functionality."""

import logging

from wakeonlan import send_magic_packet

logger = logging.getLogger(__name__)


def wake(mac_address: str, ip_address: str = "255.255.255.255", port: int = 9) -> bool:
    """
    Send a Wake-on-LAN magic packet to wake a remote machine.

    Args:
        mac_address: MAC address of the target machine (e.g., "AA:BB:CC:DD:EE:FF")
        ip_address: Broadcast IP address (default: 255.255.255.255)
        port: UDP port for WOL packet (default: 9)

    Returns:
        True if packet was sent successfully
    """
    logger.info("Sending WOL magic packet to %s via %s:%d", mac_address, ip_address, port)
    send_magic_packet(mac_address, ip_address=ip_address, port=port)
    logger.debug("WOL packet sent successfully")
    return True
