# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal S3 client for Cloudflare R2: conditional writes, reads, copy, list, multipart.

Only the calls the publisher needs, signed with AWS Signature Version 4 from the standard
library. Conditional headers (If-None-Match, If-Match, cf-copy-destination-if-none-match) are
the reason this exists: a generic sync tool cannot refuse to overwrite an object atomically.
"""
import datetime
import hashlib
import hmac
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
S3_NS = '{http://s3.amazonaws.com/doc/2006-03-01/}'


class S3Error(Exception):
    def __init__(self, status, message):
        super().__init__(f'HTTP {status}: {message}')
        self.status = status


def _quote_path(key):
    # S3 canonical URI: every byte except unreserved characters and '/' is percent-encoded.
    return urllib.parse.quote(key, safe='/-_.~')


def _quote_query(value):
    return urllib.parse.quote(value, safe='-_.~')


def sign_v4(method, url, headers, payload_sha256, access_key, secret_key, region, when, service='s3'):
    """Return the Authorization header value for one request (AWS SigV4, header-based)."""
    parsed = urllib.parse.urlsplit(url)
    amz_date = when.strftime('%Y%m%dT%H%M%SZ')
    day = when.strftime('%Y%m%d')
    lowered = {name.lower().strip(): ' '.join(str(value).strip().split()) for name, value in headers.items()}
    lowered['host'] = parsed.netloc
    lowered['x-amz-date'] = amz_date
    lowered['x-amz-content-sha256'] = payload_sha256
    names = sorted(lowered)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    canonical_query = '&'.join(f'{_quote_query(k)}={_quote_query(v)}' for k, v in sorted(query))
    canonical = '\n'.join([
        method,
        parsed.path or '/',
        canonical_query,
        ''.join(f'{name}:{lowered[name]}\n' for name in names),
        ';'.join(names),
        payload_sha256,
    ])
    scope = f'{day}/{region}/{service}/aws4_request'
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    key = ('AWS4' + secret_key).encode()
    for part in (day, region, service, 'aws4_request'):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return (f'AWS4-HMAC-SHA256 Credential={access_key}/{scope}, '
            f'SignedHeaders={";".join(names)}, Signature={signature}')


def load_credentials(bucket, environ=None, home=None):
    """Credentials of one bucket: from the environment under names that carry the bucket
    (EMAKI_R2_EMAKI_PKGS_ACCESS_KEY_ID for emaki-pkgs), else from
    ~/.config/emaki-signing/r2-<bucket>.env (0600), which holds the plain names. Each bucket has
    its own token; a name without the bucket would hand one token to both, so it is refused."""
    environ = os.environ if environ is None else environ
    names = ('EMAKI_R2_ACCOUNT_ID', 'EMAKI_R2_ACCESS_KEY_ID', 'EMAKI_R2_SECRET_ACCESS_KEY')
    scope = 'EMAKI_R2_' + bucket.upper().replace('-', '_') + '_'
    unscoped = [name for name in names if environ.get(name)]
    if unscoped:
        raise KeyError(f'{", ".join(unscoped)} in the environment would apply to every bucket; name the '
                       f'bucket: {", ".join(scope + name[len("EMAKI_R2_"):] for name in unscoped)}')
    values = {name: environ.get(scope + name[len('EMAKI_R2_'):], '') for name in names}
    if not all(values.values()):
        path = Path(home or Path.home()) / '.config/emaki-signing' / f'r2-{bucket}.env'
        if path.is_file():
            if path.stat().st_mode & 0o077:
                raise PermissionError(f'{path} must not be readable by others (chmod 600)')
            for line in path.read_text().splitlines():
                name, sep, value = line.partition('=')
                if sep and name.strip() in values and not values[name.strip()]:
                    values[name.strip()] = value.strip()
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise KeyError('R2 credentials missing: ' + ', '.join(missing))
    return values


class S3Client:
    def __init__(self, endpoint, bucket, access_key, secret_key, region='auto', opener=None, clock=None):
        self.endpoint = endpoint.rstrip('/')
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    @classmethod
    def for_r2(cls, bucket, environ=None, home=None):
        creds = load_credentials(bucket, environ, home)
        endpoint = (environ or os.environ).get('EMAKI_R2_ENDPOINT') or \
            f'https://{creds["EMAKI_R2_ACCOUNT_ID"]}.r2.cloudflarestorage.com'
        return cls(endpoint, bucket, creds['EMAKI_R2_ACCESS_KEY_ID'], creds['EMAKI_R2_SECRET_ACCESS_KEY'])

    def _url(self, key='', query=None):
        url = f'{self.endpoint}/{self.bucket}'
        if key:
            url += '/' + _quote_path(key)
        if query:
            url += '?' + '&'.join(f'{_quote_query(k)}={_quote_query(v)}' for k, v in query.items())
        return url

    def request(self, method, key='', query=None, body=b'', headers=None, ok=(200,)):
        headers = dict(headers or {})
        payload = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        url = self._url(key, query)
        when = self.clock()
        headers['Authorization'] = sign_v4(method, url, headers, payload, self.access_key,
                                           self.secret_key, self.region, when)
        headers['x-amz-date'] = when.strftime('%Y%m%dT%H%M%SZ')
        headers['x-amz-content-sha256'] = payload
        req = urllib.request.Request(url, data=body if method in ('PUT', 'POST') else None,
                                     method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            data = error.read()
            error.close()
            if error.code in ok:
                return error.code, dict(error.headers), data
            raise S3Error(error.code, data[:300].decode(errors='replace')) from None

    # Objects -------------------------------------------------------------------------
    def put(self, key, body, if_none_match=False, if_match=None, content_type='application/octet-stream',
            cache_control=None):
        headers = {'Content-Type': content_type}
        if if_none_match:
            headers['If-None-Match'] = '*'
        if if_match:
            headers['If-Match'] = if_match
        if cache_control:
            headers['Cache-Control'] = cache_control
        status, response_headers, _ = self.request('PUT', key, body=body, headers=headers, ok=(200, 412))
        return status, response_headers.get('ETag') or response_headers.get('etag')

    def get(self, key):
        status, headers, body = self.request('GET', key, ok=(200, 404))
        if status == 404:
            return None, None
        return body, headers.get('ETag') or headers.get('etag')

    def head(self, key):
        status, headers, _ = self.request('HEAD', key, ok=(200, 404))
        if status == 404:
            return None
        return headers.get('ETag') or headers.get('etag')

    def delete(self, key):
        self.request('DELETE', key, ok=(200, 204, 404))

    def copy(self, source, destination):
        """Server-side copy that refuses to replace an existing destination (412)."""
        headers = {'x-amz-copy-source': '/' + self.bucket + '/' + _quote_path(source),
                   'cf-copy-destination-if-none-match': '*'}
        status, _, _ = self.request('PUT', destination, headers=headers, ok=(200, 412))
        return status

    def list(self, prefix):
        keys, token = [], None
        while True:
            query = {'list-type': '2', 'prefix': prefix}
            if token:
                query['continuation-token'] = token
            _, _, body = self.request('GET', query=query)
            root = ET.fromstring(body)
            keys += [node.findtext(S3_NS + 'Key') for node in root.iter(S3_NS + 'Contents')]
            if root.findtext(S3_NS + 'IsTruncated') != 'true':
                return keys
            token = root.findtext(S3_NS + 'NextContinuationToken')

    # Multipart (the ISO: resumable, and visible only once complete) ---------------------
    def upload_large(self, key, path, part_size=64 * 1024 * 1024, content_type='application/octet-stream'):
        _, _, body = self.request('POST', key, query={'uploads': ''}, headers={'Content-Type': content_type})
        upload_id = ET.fromstring(body).findtext(S3_NS + 'UploadId')
        parts = []
        try:
            with open(path, 'rb') as stream:
                number = 1
                while chunk := stream.read(part_size):
                    _, headers, _ = self.request('PUT', key, query={'partNumber': str(number), 'uploadId': upload_id},
                                                 body=chunk)
                    parts.append((number, headers.get('ETag') or headers.get('etag')))
                    number += 1
            document = '<CompleteMultipartUpload>' + ''.join(
                f'<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>' for n, etag in parts
            ) + '</CompleteMultipartUpload>'
            status, _, _ = self.request('POST', key, query={'uploadId': upload_id}, body=document.encode(),
                                        headers={'If-None-Match': '*'}, ok=(200, 412))
            if status == 412:
                raise S3Error(412, f'{key} already exists')
        except BaseException:
            self.request('DELETE', key, query={'uploadId': upload_id}, ok=(200, 204, 404))
            raise


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
