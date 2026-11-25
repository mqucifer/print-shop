"""Made-up certificates for the phone's page over HTTPS (mqucifer/print-shop#312), made in the
test: never a real name, domain or key. `.test` is reserved for tests (RFC 2606)."""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from print_shop.phone.tls import TlsFiles

#: The page's made-up name.
NAME = "print.made-up.test"


def make(folder: Path, names: tuple[str, ...] = (NAME,), *, starts: timedelta = timedelta(days=-1),
         lasts: timedelta = timedelta(days=60), key: ec.EllipticCurvePrivateKey | None = None,
         ) -> TlsFiles:
    """A self-signed certificate for `names`, valid from `starts` to `lasts` from now, and its
    key, written as `cert.pem` and `key.pem` in `folder`."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "none")])
    built = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now + starts).not_valid_after(now + lasts)
             .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                            critical=False)
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True))
    if names:
        built = built.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
    cert = built.sign(key, hashes.SHA256())
    folder.mkdir(parents=True, exist_ok=True)
    files = TlsFiles(folder / "cert.pem", folder / "key.pem")
    files.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    files.key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                            serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    return files


def phone_context(files: TlsFiles) -> ssl.SSLContext:
    """A phone's browser that trusts the made-up certificate, and checks its name and dates."""
    context = ssl.create_default_context(cadata=files.cert.read_text())
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


def served(host: str, port: int, files: TlsFiles, name: str = NAME) -> x509.Certificate:
    """The certificate the page serves at `host`, as a phone that trusts `files` sees it."""
    with socket.create_connection((host, port), timeout=5) as raw, \
            phone_context(files).wrap_socket(raw, server_hostname=name) as conn:
        der = conn.getpeercert(binary_form=True)
    assert der is not None
    return x509.load_der_x509_certificate(der)
