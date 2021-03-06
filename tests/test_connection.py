import pytest
import socket
import time

from contextlib import closing
from serfclient import connection


def extract_addr(rpc, ip_address, address_family=socket.AF_INET6):
    packed_ip_format = socket.inet_pton(address_family, ip_address)
    r = rpc._decode_addr_key({b'Addr': packed_ip_format})
    return r[b'Addr'].decode('utf-8')


class TestSerfConnection(object):
    """
    Tests for the Serf RPC communication object.
    """
    @pytest.yield_fixture
    def rpc(self):
        with closing(connection.SerfConnection()) as conn:
            yield conn

    def test_has_a_default_host_port_and_timeout(self, rpc):
        assert rpc.host == 'localhost'
        assert rpc.port == 7373
        assert rpc.timeout == 3

    def test_allows_passing_host_port_and_timeout(self):
        with closing(
                connection.SerfConnection(host='foo', port=455, timeout=500)
        ) as rpc:
            assert rpc.host == 'foo'
            assert rpc.port == 455
            assert rpc.timeout == 500

    def test_representation(self, rpc):
        assert str(rpc) == \
            'SerfConnection<counter=0,host=localhost,port=7373,timeout=3>'

    def test_connection_to_bad_socket_throws_exception(self):
        with closing(connection.SerfConnection(port=40000)) as rpc:
            with pytest.raises(
                    connection.SerfConnectionError
            ) as exceptionInfo:
                rpc.handshake()
            assert 'connecting localhost:40000. Connection refused.' \
                in str(exceptionInfo)

    def test_handshake_to_serf_agent(self, rpc):
        assert rpc.handshake().head == {b'Seq': 0, b'Error': b''}

    def test_call_throws_exception_if_socket_none(self, rpc):
        with pytest.raises(connection.SerfConnectionError) as exceptionInfo:
            rpc.call('members')
        assert 'handshake must be made first' in str(exceptionInfo)

    def test_handshake_and_call_increments_counter(self, rpc):
        assert 'counter=0' in str(rpc)
        rpc.handshake()
        assert 'counter=1' in str(rpc)
        rpc.call('event',
                 {"Name": "foo", "Payload": "test payload", "Coalesce": True},
                 expect_body=False)
        assert 'counter=2' in str(rpc)

    def test_msgpack_object_stream_decode(self, rpc):
        rpc.handshake()
        result = rpc.call('members')
        assert result.head == {b'Error': b'', b'Seq': 1}
        assert b'Members' in result.body.keys()

    def test_small_socket_recv_size(self, rpc):
        # Read a paltry 7 bytes at a time, intended to stress the buffered
        # socket reading and msgpack message handling logic.
        rpc.handshake()
        rpc._socket_recv_size = 7
        result = rpc.call('members')
        assert result.head == {b'Error': b'', b'Seq': 1}
        assert b'Members' in result.body.keys()

    def test_rpc_timeout(self, rpc):
        # Avoid delaying the test too much.
        rpc.timeout = 0.1
        rpc.handshake()
        with pytest.raises(connection.SerfTimeout):
            # Incorrectly set expect_body to True for an event RPC,
            # which will wait around for a body it'll never get,
            # which should cause a SerfTimeout exception.
            rpc.call('event',
                     {"Name": "foo", "Payload": "test payload",
                      "Coalesce": True},
                     expect_body=True)

    def test_rxing_too_many_messages(self, rpc):
        rpc.handshake()

        # Sneakily send two members requests using the socket directly, and
        # then don't read from the socket in order to make sure the socket
        # receive buffer has many more responses than expected the next time
        # a real RPC call is made.
        for index in range(2):
            rpc._socket.sendall(
                connection.msgpack.packb({"Seq": rpc._counter(),
                                          "Command": "members"}))

        # Allow serf a moment to make sure it has responded to both requests
        # and that the socket recieve buffer has the content.
        time.sleep(0.05)

        # This call should recieve all the previous responses at once
        # and it should fail.
        with pytest.raises(connection.SerfProtocolError):
            rpc.call('members')

    def test_connection_closed(self, rpc):
        rpc.handshake()

        # Mock socket.recv returning an empty string, as it does when the
        # connection has been closed.
        class MockSocket:
            def sendall(self, content):
                pass

            def recv(self, size):
                return ""

            def close(self):
                pass

        rpc._socket = MockSocket()

        with pytest.raises(connection.SerfConnectionError):
            rpc.handshake()

    def test_decode_addr_key_ipv6(self, rpc):
        ip_address = '2001:a:b:c:1:2:3:4'
        assert extract_addr(rpc, ip_address) == ip_address

    def test_decode_addr_key_ipv4_mapped_ipv6(self, rpc):
        assert extract_addr(rpc, '::ffff:192.168.0.1') == '192.168.0.1'

    def test_decode_addr_key_ipv4(self, rpc):
        ip_address = '192.168.0.1'
        assert extract_addr(rpc, ip_address, socket.AF_INET) == ip_address

    def test_close(self, rpc):
        rpc.handshake()
        assert rpc._socket is not None
        rpc.close()
        assert rpc._socket is None
