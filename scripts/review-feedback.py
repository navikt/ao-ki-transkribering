#!/usr/bin/env python3
"""List feedback or download one case using the reviewer's own GCP credentials."""
import argparse
import hashlib
import json
import os
import re

from google.cloud import storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='Print bounded case metadata; no comments or meeting content')
    listing.add_argument('--limit', type=int, default=20)
    download = commands.add_parser('download', help='Download one complete package to a new owner-only file')
    download.add_argument('case_id')
    download.add_argument('--output', required=True)
    download.add_argument('--audio-output', help='Also download the attached audio to this new owner-only file')
    args = parser.parse_args()
    if args.command == 'list' and not 1 <= args.limit <= 100:
        parser.error('--limit must be between 1 and 100')
    if args.command == 'download' and not re.fullmatch('[0-9a-f]{64}', args.case_id):
        parser.error('invalid case ID')
    client = storage.Client()
    if args.command == 'list':
        for blob in client.list_blobs(args.bucket, prefix='cases/', max_results=args.limit):
            data = json.loads(blob.download_as_bytes(timeout=20))
            print(json.dumps({key: value for key, value in {
                'case_id': data['case_id'], 'submitted_at': data['submitted_at'],
                'rating': data['feedback']['rating'], 'category': data['feedback']['category'],
                'attachments': bool(data['attachments']),
            }.items()}))
    else:
        raw = client.bucket(args.bucket).blob(f'cases/{args.case_id}.json').download_as_bytes(timeout=20)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
        if args.audio_output:
            audio = (json.loads(raw).get('attachments') or {}).get('audio')
            if not audio or audio.get('object') != f'audio/{args.case_id}':
                raise ValueError('No matching audio attachment')
            audio_fd = os.open(args.audio_output, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(audio_fd, 'w+b') as output:
                client.bucket(args.bucket).blob(audio['object']).download_to_file(output, timeout=60)
                output.seek(0)
                checksum = hashlib.file_digest(output, 'sha256').hexdigest()
                if checksum != audio['sha256']:
                    raise ValueError('Audio checksum mismatch')
        print('Downloaded case. Local copies are outside bucket cleanup.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Avoid exposing cloud SDK exception/request contents.
        raise SystemExit('Review failed. Check credentials, bucket, permissions and output path.') from None
