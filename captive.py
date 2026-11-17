"""Captive portal: every address on the tree's hotspot leads to the tree.

Two halves.

**DNS.** CircuitPython's access point tells every phone that joins "your DNS
server is me" (its DHCP server writes option 6 with its own address,
unconditionally), so all name lookups arrive here.  :class:`CaptiveDNS`
answers every one of them with the board's own address, which is what makes
anything typed or tapped end up at the tree.

**The probe URLs.** Phones decide whether a network is usable by fetching a
known URL over plain HTTP and checking the answer.  Give them something
unexpected and the operating system raises its "sign in to this network"
page; give them exactly what they expect and it goes quiet.  So before a
phone has seen the tree it gets a redirect, and afterwards it gets the
answer it wants, which stops it nagging about there being no internet and
stops Android offering to drop back to mobile data.

Nothing here runs when the board is joined to a home network instead of
running its own hotspot: hijacking somebody's DNS on their own wifi would
be plain wrong.
"""

#: What each system asks for, and the answer that satisfies it.  The host
#: does not matter -- DNS has already pointed all of them here -- so these
#: are matched on the path alone.
PROBES = {
    # Android, ChromeOS, Chrome
    "/generate_204": (204, "", "text/plain"),
    "/gen_204": (204, "", "text/plain"),
    # iOS, iPadOS, macOS
    "/hotspot-detect.html": (
        200,
        "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>",
        "text/html",
    ),
    "/library/test/success.html": (
        200,
        "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>",
        "text/html",
    ),
    # Windows
    "/connecttest.txt": (200, "Microsoft Connect Test", "text/plain"),
    "/ncsi.txt": (200, "Microsoft NCSI", "text/plain"),
    # Firefox
    "/success.txt": (200, "success\n", "text/plain"),
    "/canonical.html": (
        200,
        '<meta http-equiv="refresh" content="0;url=https://support.mozilla.org/kb/captive-portal"/>',
        "text/html",
    ),
}


def is_probe(path):
    return path in PROBES


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------
#: Classic DNS over UDP is capped at 512 bytes; anything longer is not a
#: question we need to answer.
MAX_PACKET = 512

_TYPE_A = 1
_TYPE_ANY = 255
_CLASS_IN = 1
_CLASS_ANY = 255


def pack_address(text):
    """"192.168.4.1" -> b'\\xc0\\xa8\\x04\\x01'."""
    parts = str(text).split(".")
    if len(parts) != 4:
        raise ValueError("not an IPv4 address: %r" % (text,))
    out = bytearray(4)
    for i, part in enumerate(parts):
        value = int(part)
        if value < 0 or value > 255:
            raise ValueError("not an IPv4 address: %r" % (text,))
        out[i] = value
    return bytes(out)


def _question_end(query):
    """Index just past the question, or None if the packet is malformed.

    Every length here comes off the wire, so each step is bounded before it
    is used: a question that runs off the end, nests too deep, or never
    terminates is rejected rather than trusted.
    """
    at = 12
    length_so_far = 0
    while True:
        if at >= len(query):
            return None
        length = query[at]
        if length == 0:
            at += 1
            break
        if length & 0xC0:
            # A compression pointer ends the name; it is two bytes long.
            at += 2
            break
        at += 1 + length
        length_so_far += 1 + length
        if length_so_far > 255:
            return None
    if at + 4 > len(query):
        return None
    return at


def build_reply(query, address_bytes, ttl=30):
    """Answer a DNS query with ``address_bytes``.  None means stay silent.

    Questions we cannot answer with an A record -- an AAAA lookup, say --
    get an empty NOERROR rather than a wrong answer, which is what makes a
    client fall back to IPv4 instead of giving up.
    """
    if query is None or len(query) < 12 or len(query) > MAX_PACKET:
        return None

    flags_high = query[2]
    if flags_high & 0x80:
        return None  # already a reply; not ours to answer
    if (flags_high >> 3) & 0x0F:
        return None  # not a standard query

    questions = (query[4] << 8) | query[5]
    if questions != 1:
        return None

    end = _question_end(query)
    if end is None:
        return None

    qtype = (query[end] << 8) | query[end + 1]
    qclass = (query[end + 2] << 8) | query[end + 3]
    answerable = qtype in (_TYPE_A, _TYPE_ANY) and qclass in (_CLASS_IN, _CLASS_ANY)

    reply = bytearray()
    reply += query[0:2]                      # same id
    reply.append(0x84 | (query[2] & 0x01))   # response, authoritative, copy RD
    reply.append(0x80)                       # recursion available, no error
    reply += b"\x00\x01"                     # one question, echoed below
    reply += b"\x00\x01" if answerable else b"\x00\x00"
    reply += b"\x00\x00\x00\x00"             # no authority, no additional
    reply += query[12:end + 4]               # the question, verbatim

    if answerable:
        reply += b"\xc0\x0c"                 # name: pointer back to the question
        reply += b"\x00\x01\x00\x01"         # type A, class IN
        reply += bytes((
            (ttl >> 24) & 0xFF, (ttl >> 16) & 0xFF, (ttl >> 8) & 0xFF, ttl & 0xFF,
        ))
        reply += b"\x00\x04"                 # four bytes of address
        reply += address_bytes

    return bytes(reply)


class CaptiveDNS:
    """A UDP socket on port 53 that points every name at the board."""

    def __init__(self, pool, address, port=53, ttl=30):
        self.address = str(address)
        self.packed = pack_address(self.address)
        self.ttl = ttl
        self.answered = 0
        self.ignored = 0
        self._buffer = bytearray(MAX_PACKET)
        self.socket = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.socket.bind((self.address, port))

    def poll(self):
        """Answer one waiting query.  True if there was one."""
        try:
            size, client = self.socket.recvfrom_into(self._buffer)
        except OSError:
            return False  # nothing waiting
        except Exception:
            return False

        if not size:
            return False

        reply = build_reply(bytes(self._buffer[:size]), self.packed, self.ttl)
        if reply is None:
            self.ignored += 1
            return True
        try:
            self.socket.sendto(reply, client)
            self.answered += 1
        except Exception:
            pass
        return True

    def close(self):
        try:
            self.socket.close()
        except Exception:
            pass
