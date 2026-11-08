# Copyright (C) 2021-2026 Luis López <luis@cuarentaydos.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.

"""ideenergy clients that never print the full username (email) in logs."""

import ideenergy

MASK_VISIBLE_CHARS = 3
MASK = "***"


def mask_username(username: str | None) -> str:
    """Mask an email-like username: 'jaume@example.com' -> 'jau***@example.com'."""
    if not username:
        return MASK

    local, sep, domain = username.partition("@")
    return f"{local[:MASK_VISIBLE_CHARS]}{MASK}{sep}{domain}"


class MaskedUsernameMixin:
    """Override str/repr so library and integration logs mask the username.

    ideenergy logs with f"{self}", so overriding __str__ also covers the
    library's own log lines.
    """

    username: str
    contract: str | None

    def __str__(self) -> str:
        masked = mask_username(self.username)
        return masked + (f"/{self.contract}" if self.contract else "")

    def __repr__(self) -> str:
        return (
            f"<ideenergy.Client "
            f"username={mask_username(self.username)}, "
            f"contract={self.contract or '(none)'}>"
        )


class Client(MaskedUsernameMixin, ideenergy.Client):
    pass


class MockClient(MaskedUsernameMixin, ideenergy.MockClient):
    pass
