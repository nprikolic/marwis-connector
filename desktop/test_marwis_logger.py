"""Tests against the byte-level examples documented in the Lufft manuals.

Vectors:
  - UMB-Protokoll_1_0_Version_1_7_e.pdf, ch. 3.10 (CRC 61D9h example)
  - Marwis-UserManual_34_en.pdf, appendix 19.2.4 / 19.2.5 (recorded frames)
"""

import struct
import unittest

from marwis_logger import build_frame, parse_frame, crc16, UMB_TYPES


def hx(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


class TestCrc(unittest.TestCase):
    def test_mcrf4xx_check_value(self):
        # Canonical CRC16-MCRF4XX check value
        self.assertEqual(crc16(b"123456789"), 0x6F91)

    def test_umb_doc_request_example(self):
        # UMB spec ch. 3.11 request example states "CRC is 61D9h" / wire D9 61 —
        # that is a byte-swap erratum in the document. The correct value 0xD961
        # is confirmed by the spec's own response example (8690h, below), both
        # recorded MARWIS frames, and the canonical check value above.
        data = hx("01 10 01 70 01 F0 04 02 23 10 64 00 03")
        self.assertEqual(crc16(data), 0xD961)

    def test_umb_doc_response_example(self):
        # UMB spec ch. 3.11 response example: checksum 8690h, wire 90 86
        data = hx("01 10 01 F0 01 70 0A 02 23 10 00 64 00 16 F5 54 E1 41 03")
        self.assertEqual(crc16(data), 0x8690)


class TestBuildFrame(unittest.TestCase):
    def test_single_channel_request_marwis_manual(self):
        # Manual 19.2.4: online data query (23h) for channel 100, addr A001h
        expected = hx("01 10 01 A0 01 F0 04 02 23 10 64 00 03 BE F8 04")
        frame = build_frame(0xA001, 0x23, 0x10, struct.pack("<H", 100))
        self.assertEqual(frame, expected)

    def test_multi_channel_request_marwis_manual(self):
        # Manual 19.2.5: multi-channel query (2Fh) for channels 100 and 900
        expected = hx("01 10 01 A0 01 F0 07 02 2F 10 02 64 00 84 03 03 C1 26 04")
        payload = bytes([2]) + struct.pack("<H", 100) + struct.pack("<H", 900)
        frame = build_frame(0xA001, 0x2F, 0x10, payload)
        self.assertEqual(frame, expected)


class TestParseFrame(unittest.TestCase):
    def test_single_channel_response(self):
        # Manual 19.2.4: response, road temp 24.36 degC as float
        raw = hx("01 10 01 F0 01 A0 0A 02 23 10 00 64 00 16 C3 D8 C2 41 03 BA 2C 04")
        from_addr, cmd, status, payload = parse_frame(raw)
        self.assertEqual(from_addr, 0xA001)
        self.assertEqual(cmd, 0x23)
        self.assertEqual(status, 0x00)
        self.assertEqual(struct.unpack("<H", payload[0:2])[0], 100)
        fmt, size = UMB_TYPES[payload[2]]
        value = struct.unpack(fmt, payload[3:3 + size])[0]
        self.assertAlmostEqual(value, 24.36, places=2)

    def test_multi_channel_response(self):
        # Manual 19.2.5: response with channel 100 (float) and 900 (uint8 = damp)
        raw = hx("01 10 01 F0 01 A0 13 02 2F 10 00 02"
                 " 08 00 64 00 16 CB 3D A5 41"
                 " 05 00 84 03 10 01"
                 " 03 3F 77 04")
        _, cmd, status, data = parse_frame(raw)
        self.assertEqual(cmd, 0x2F)
        self.assertEqual(status, 0x00)
        count, pos = data[0], 1
        self.assertEqual(count, 2)
        results = []
        for _ in range(count):
            sub_len = data[pos]
            sub = data[pos + 1: pos + 1 + sub_len]
            pos += 1 + sub_len
            channel = struct.unpack("<H", sub[1:3])[0]
            fmt, size = UMB_TYPES[sub[3]]
            results.append((channel, struct.unpack(fmt, sub[4:4 + size])[0]))
        self.assertEqual(results[0][0], 100)
        self.assertAlmostEqual(results[0][1], 20.655, places=2)
        self.assertEqual(results[1], (900, 1))  # 1 = damp

    def test_crc_error_detected(self):
        raw = bytearray(hx("01 10 01 F0 01 A0 0A 02 23 10 00 64 00 16 C3 D8 C2 41 03 BA 2C 04"))
        raw[14] ^= 0xFF  # corrupt one value byte
        from marwis_logger import UmbError
        with self.assertRaises(UmbError):
            parse_frame(bytes(raw))


if __name__ == "__main__":
    unittest.main()
