import httplib, json
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado import gen

from core import logger
from core.config import config
from core.events import events


def init():
    config.SLACK_ENABLE = config.read_config_var('slack', 'enable', False, 'bool')
    config.SLACK_URL = config.read_config_var('slack', 'url', False, 'str')
    config.SLACK_CHANNEL = config.read_config_var('slack', 'channel', False, 'str')
    if config.SLACK_ENABLE and config.SLACK_URL and config.SLACK_CHANNEL:
        config.SLACK_USERNAME = config.read_config_var('slack', 'username', 'alarm-bot', 'str')
        config.SLACK_IGNOREZONES = config.read_config_var('slack', 'ignorezones', [], 'listint')
        config.SLACK_IGNOREPARTITIONS = config.read_config_var('slack', 'ignorepartitions', [], 'listint')
        logger.debug('Slack Enabled - Partitions Ignored: %s - Zones Ignored: %s'
            % (",".join([str (i) for i in config.SLACK_IGNOREPARTITIONS]), ",".join([str(i) for i in config.SLACK_IGNOREZONES])))
        events.register('statechange', sendNotification, config.SLACK_IGNOREPARTITIONS, config.SLACK_IGNOREZONES)

@gen.coroutine
def sendNotification(eventType, type, parameters, code, event, message, defaultStatus):
    http_client = AsyncHTTPClient()
    body = json.dumps({
        "channel": config.SLACK_CHANNEL,
        "username": config.SLACK_USERNAME,
        "text": str(message)})
    res = yield http_client.fetch(config.SLACK_URL, method='POST', headers={"Content-type": "application/json"}, body=body)
    logger.debug('Slack notification sent')
