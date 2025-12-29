# zashterminal/ui/ssh_dialogs.py
"""SSH connection error dialogs with detailed error messages."""

from typing import Optional

from ..utils.translation_utils import _


# SSH exit codes and their meanings
# Reference: https://man.openbsd.org/ssh.1#EXIT_STATUS
SSH_ERROR_CODES = {
    1: (
        "general_error",
        _("General SSH Error"),
        _("The SSH command failed with a general error."),
    ),
    2: ("misuse", _("Command Misuse"), _("SSH command was used incorrectly.")),
    5: (
        "auth_failed",
        _("Authentication Failed"),
        _("Username or password is incorrect, or the user is not allowed to connect."),
    ),
    6: (
        "auth_failed",
        _("Authentication Failed"),
        _("Authentication failed. Check username, password, or SSH key."),
    ),
    65: (
        "host_key_failed",
        _("Host Key Verification Failed"),
        _("The server's host key is not recognized or has changed."),
    ),
    255: (
        "connection_failed",
        _("Connection Failed"),
        _(
            "Could not establish connection to the server. Check if the host is reachable and the port is correct."
        ),
    ),
}

# Patterns to detect specific errors from terminal output (lowercase)
# These patterns are matched against the terminal output to provide more specific error messages
ERROR_PATTERNS = {
    # Authentication errors
    "permission denied": (
        "auth_failed",
        _("Authentication Failed"),
        _("Username or password is incorrect. Please verify your credentials."),
    ),
    "authentication failed": (
        "auth_failed",
        _("Authentication Failed"),
        _("Authentication failed. Check your username and password."),
    ),
    "password:": (
        "auth_failed",
        _("Authentication Failed"),
        _("Password authentication failed. The password may be incorrect."),
    ),
    "incorrect password": (
        "auth_failed",
        _("Authentication Failed"),
        _("The password entered is incorrect."),
    ),
    "senha:": (
        "auth_failed",
        _("Authentication Failed"),
        _("Password authentication failed. The password may be incorrect."),
    ),
    "senha incorreta": (
        "auth_failed",
        _("Authentication Failed"),
        _("The password entered is incorrect."),
    ),
    # sshpass specific errors
    "invalid/incorrect password": (
        "auth_failed",
        _("Authentication Failed"),
        _("The password is invalid or incorrect."),
    ),
    # Multiple auth method failures
    "publickey,password": (
        "auth_multi_failed",
        _("Multiple Auth Methods Failed"),
        _(
            "Both public key and password authentication failed. Check your credentials."
        ),
    ),
    "permission denied (publickey)": (
        "key_rejected",
        _("SSH Key Rejected"),
        _(
            "The server rejected your SSH key. The key may not be authorized on the server."
        ),
    ),
    # SSH Agent errors
    "agent refused operation": (
        "agent_error",
        _("SSH Agent Error"),
        _("SSH agent refused the operation. Check if your key is loaded with ssh-add."),
    ),
    "could not open a connection to your authentication agent": (
        "agent_error",
        _("SSH Agent Not Running"),
        _(
            "SSH agent is not running. Start it with eval $(ssh-agent) and add your key."
        ),
    ),
    # Host key verification
    "host key verification failed": (
        "host_key_failed",
        _("Host Key Verification Failed"),
        _(
            "The server's host key has changed or is not trusted. This could indicate a security issue."
        ),
    ),
    "offending key": (
        "host_key_changed",
        _("Host Key Changed"),
        _(
            "The server's host key has changed. Remove the old key from known_hosts if this is expected."
        ),
    ),
    "remote host identification has changed": (
        "host_key_changed",
        _("Host Key Changed"),
        _(
            "The server's identification has changed. This could indicate a security issue or server reinstall."
        ),
    ),
    # DNS errors
    "could not resolve hostname": (
        "dns_error",
        _("DNS Resolution Failed"),
        _("Could not resolve the hostname. Check if the address is correct."),
    ),
    "name or service not known": (
        "dns_error",
        _("DNS Resolution Failed"),
        _("The hostname could not be resolved. Check the server address."),
    ),
    "no address associated": (
        "dns_error",
        _("DNS Resolution Failed"),
        _("No address found for the hostname."),
    ),
    "temporary failure in name resolution": (
        "dns_error",
        _("DNS Temporary Failure"),
        _("DNS resolution temporarily failed. Check your network connection."),
    ),
    # Connection errors
    "connection refused": (
        "connection_refused",
        _("Connection Refused"),
        _("The server refused the connection. Check if SSH is running on the server."),
    ),
    "connection timed out": (
        "timeout",
        _("Connection Timeout"),
        _(
            "Connection attempt timed out. The server may be unreachable or behind a firewall."
        ),
    ),
    "operation timed out": (
        "timeout",
        _("Connection Timeout"),
        _("The operation timed out. Server may be slow or unreachable."),
    ),
    "no route to host": (
        "no_route",
        _("No Route to Host"),
        _(
            "Network path to the server could not be found. Check your network connection."
        ),
    ),
    "network is unreachable": (
        "network_unreachable",
        _("Network Unreachable"),
        _("The network is not reachable. Check your internet connection."),
    ),
    "network unreachable": (
        "network_unreachable",
        _("Network Unreachable"),
        _("The network is not reachable. Check your internet connection."),
    ),
    # Connection closed
    "connection reset by peer": (
        "remote_closed",
        _("Connection Reset"),
        _("The connection was reset by the remote server."),
    ),
    "connection closed by remote host": (
        "remote_closed",
        _("Connection Closed"),
        _("The remote server closed the connection unexpectedly."),
    ),
    "broken pipe": (
        "remote_closed",
        _("Connection Lost"),
        _("The connection to the server was lost."),
    ),
    "connection closed by": (
        "remote_closed",
        _("Connection Closed"),
        _("The server closed the connection."),
    ),
    "read from socket failed": (
        "remote_closed",
        _("Connection Lost"),
        _("Failed to read from the connection. The server may have closed it."),
    ),
    # Auth limit
    "too many authentication failures": (
        "auth_limit",
        _("Too Many Authentication Failures"),
        _("Too many failed login attempts. Wait before trying again."),
    ),
    "received disconnect from": (
        "server_disconnect",
        _("Server Disconnected"),
        _("The server disconnected. Check server logs for details."),
    ),
    # Port issues
    "port 22: connection refused": (
        "ssh_not_running",
        _("SSH Service Not Running"),
        _("SSH service doesn't appear to be running on the server."),
    ),
    # Key auth issues
    "no such identity": (
        "key_auth_failed",
        _("Key Authentication Failed"),
        _("The specified SSH key file was not found."),
    ),
    "invalid key": (
        "key_auth_failed",
        _("Key Authentication Failed"),
        _("The SSH key is invalid or corrupted."),
    ),
    "load key": (
        "key_auth_failed",
        _("Key Authentication Failed"),
        _("Failed to load the SSH key file."),
    ),
    "bad permissions": (
        "key_permissions",
        _("Key File Permissions Error"),
        _("SSH key file has incorrect permissions. Use chmod 600 on the key file."),
    ),
    "key_load_public: invalid format": (
        "key_format_error",
        _("SSH Key Format Error"),
        _("The SSH key file has an invalid format."),
    ),
    # Certificate errors
    "certificate signature failed": (
        "cert_invalid",
        _("Certificate Invalid"),
        _("The SSH certificate signature verification failed."),
    ),
    "certificate has expired": (
        "cert_expired",
        _("Certificate Expired"),
        _("The SSH certificate has expired and needs renewal."),
    ),
    # Server-side errors
    "administratively prohibited": (
        "server_denied",
        _("Connection Denied by Server"),
        _("The server administrator has blocked this connection."),
    ),
    "subsystem request failed": (
        "sftp_unavailable",
        _("SFTP Not Available"),
        _(
            "SFTP subsystem is not available on the server. Contact the server administrator."
        ),
    ),
    "channel 0: open failed": (
        "channel_failed",
        _("SSH Channel Failed"),
        _("Failed to open SSH channel. The server may be overloaded or misconfigured."),
    ),
    # sshpass-specific errors
    "sshpass: permission denied": (
        "sshpass_denied",
        _("Password Authentication Denied"),
        _(
            "Password authentication was denied. The server may not allow password login."
        ),
    ),
}


def get_error_info(exit_code: int, terminal_text: Optional[str] = None) -> tuple:
    """
    Determine the error type and messages based on exit code and terminal output.

    Returns:
        Tuple of (error_type, title, description)
    """
    # First, try to match patterns in terminal output (more specific)
    if terminal_text:
        text_lower = terminal_text.lower()
        for pattern, error_info in ERROR_PATTERNS.items():
            if pattern in text_lower:
                return error_info

    # Fall back to exit code
    if exit_code in SSH_ERROR_CODES:
        return SSH_ERROR_CODES[exit_code]

    # Generic error
    return (
        "unknown",
        _("SSH Connection Error"),
        _("An unexpected error occurred while connecting. Exit code: {code}").format(
            code=exit_code
        ),
    )
