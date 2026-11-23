"""Drives the corpus publisher's own Publisher and Transport against an origin, through the Transport's loopback test
seam (allow_loopback, a constructor argument no configuration reaches). Source and fixture proof only.

  python3 publish.py publish <state-dir> <origin> <repository> <title>   commit or confirm a cut with this title
  python3 publish.py status  <state-dir> <origin> <repository>
  python3 publish.py retry   <state-dir> <origin> <repository> <title>   resend the acknowledged cut exactly

The token comes from HOSTED_PUBLISH_TOKEN. Each command prints one JSON object.
"""
import copy, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'skills/delivery-views/scripts'))
import hosted  # noqa: E402
import public_projection as public  # noqa: E402
import views_common as views  # noqa: E402

FIXTURE = json.loads((ROOT / 'tests/fixtures/delivery-views/hosted/valid-dissent-markup.json').read_text(encoding='utf-8'))
# The identity binds the configured origin; the Transport reaches the loopback server in its place.
CONFIG = {'spec': 'delivery-views-config/1', 'hosted': {'origin': 'https://deliveries.example.com', 'repositoryId': None, 'tokenEnv': 'HOSTED_PUBLISH_TOKEN'}}


def cut(title):
    value = copy.deepcopy(FIXTURE)
    value['document']['state']['queue'][0]['title'] = title
    return value


def main(argv):
    command, state, origin, repository = argv[:4]
    token = os.environ['HOSTED_PUBLISH_TOKEN']
    config = copy.deepcopy(CONFIG)
    config['hosted']['repositoryId'] = repository
    config = views.validate_config(config)['hosted']
    Path(state).mkdir(parents=True, exist_ok=True)
    publisher = hosted.Publisher(Path(state), config, 'APP-1', secret_values=[token])
    publisher.api = hosted.Transport(origin, repository, publisher.identity['publicId'], token, allow_loopback=True)
    out = {'publicId': publisher.identity['publicId']}
    if command == 'publish':
        out['result'] = publisher.step(lambda: cut(argv[4]))
    elif command == 'status':
        out['status'] = publisher.api('GET', 'status')
    elif command == 'retry':
        last = publisher.identity['receipt']
        data = public.encode(cut(argv[4]))
        headers = {'X-Writer-Epoch': str(last['epoch']), 'X-Snapshot-Sequence': str(last['sequence']),
                   'X-Snapshot-Digest': last['digest'], 'X-Renderer-Version': hosted.RENDERER}
        out['receipt'] = publisher.api('POST', 'snapshots', data, headers)
        out['acknowledged'] = last
    else:
        raise SystemExit(f'unknown command {command}')
    print(json.dumps(out))


if __name__ == '__main__':
    main(sys.argv[1:])
