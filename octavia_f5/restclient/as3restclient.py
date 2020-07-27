# Copyright 2018 SAP SE
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import json
import time

import futurist
import prometheus_client as prometheus
from oslo_log import log as logging
from six.moves.urllib import parse

from octavia_f5.restclient.as3classes import AS3
from octavia_f5.restclient.bigip import bigip_auth, bigip_restclient
from octavia_f5.utils import exceptions

LOG = logging.getLogger(__name__)
AS3_PATH = '/mgmt/shared/appsvcs'
AS3_DECLARE_PATH = AS3_PATH + '/declare'
AS3_INFO_PATH = AS3_PATH + '/info'
AS3_TASKS_PATH = AS3_PATH + '/task/{}'

ASYNC_TIMEOUT = 90  # 90 seconds
AS3_TASK_POLL_INTERVAL = 5

class AS3RestClient(bigip_restclient.BigIPRestClient):
    """ AS3 rest client, implements POST, PATCH and DELETE operation for talking to F5 localhost AS3.
        Also supports BigIP rest calls to icontrol REST.

        See: https://clouddocs.f5.com/products/extensions/f5-appsvcs-extension/latest/refguide/as3-api.html
    """
    _metric_post_duration = prometheus.metrics.Summary(
        'octavia_as3_post_duration', 'Time it needs to send a POST request to AS3')
    _metric_post_exceptions = prometheus.metrics.Counter(
        'octavia_as3_post_exceptions', 'Number of exceptions at POST requests sent to AS3')
    _metric_patch_duration = prometheus.metrics.Summary(
        'octavia_as3_patch_duration', 'Time it needs to send a PATCH request to AS3')
    _metric_patch_exceptions = prometheus.metrics.Counter(
        'octavia_as3_patch_exceptions', 'Number of exceptions at PATCH request sent to AS3')
    _metric_delete_duration = prometheus.metrics.Summary(
        'octavia_as3_delete_duration', 'Time it needs to send a DELETE request to AS3')
    _metric_delete_exceptions = prometheus.metrics.Counter(
        'octavia_as3_delete_exceptions', 'Number of exceptions at DELETE request sent to AS3')
    _metric_version = prometheus.Info(
        'octavia_as3_version', 'AS3 Version')
    task_watcher = None

    def __init__(self, bigip_url, verify=True, auth=None, async_mode=False):
        if async_mode:
            self.task_watcher = futurist.ThreadPoolExecutor(max_workers=1)

        super(AS3RestClient, self).__init__(bigip_url, verify, auth)

    def debug_enable(self):
        """ Installs requests hook to enable debug logs of AS3 requests and responses. """

        def log_response(r, *args, **kwargs):
            # redact credentials from url
            url = parse.urlparse(r.url)
            redacted_url = url._replace(netloc=url.hostname)

            LOG.debug("%s %s finished with code %s", r.request.method, redacted_url.geturl(), r.status_code)
            if r.request.body:
                LOG.debug("Request Body")
                try:
                    parsed = json.loads(r.request.body)
                    LOG.debug("%s", json.dumps(parsed, sort_keys=True, indent=4))
                except ValueError:
                    LOG.debug("%s", r.request.body)

            LOG.debug("Response")
            if 'application/json' in r.headers.get('Content-Type'):
                try:
                    parsed = r.json()
                    if 'results' in parsed:
                        parsed = parsed['results']
                    LOG.debug("%s", json.dumps(parsed, sort_keys=True, indent=4))
                except ValueError:
                    LOG.error("Valid JSON expected: %s", r.text)
            else:
                LOG.debug("%s", r.text)

        LOG.debug("Installing AS3 debug hook for '%s'", self.hostname)
        self.hooks['response'].append(log_response)

    def wait_for_task_finished(self, task_id):
        """ Waits for AS3 task to be finished successfully
        :param task_id: task id to be fetched
        :return: request result
        """
        while True:
            task = super(AS3RestClient, self).get(AS3_TASKS_PATH.format(task_id))
            if task.ok and all(res['code'] != 0 for res in task.json()['results']):
                return task
            time.sleep(AS3_TASK_POLL_INTERVAL)

    @_metric_post_exceptions.count_exceptions()
    @_metric_post_duration.time()
    def post(self, tenants, payload):
        url = '{}/{}'.format(self.get_url(AS3_DECLARE_PATH), ','.join(tenants))
        if not self.task_watcher:
            return super(AS3RestClient, self).post(url, json=payload.to_dict())

        # ASYNC Mode enabled
        r = super(AS3RestClient, self).post(url, json=payload.to_dict(), params={'async': 'true'})
        if r.ok:
            task_id = r.json()['id']
            fut = self.task_watcher.submit(self.wait_for_task_finished, task_id)
            return fut.result(timeout=ASYNC_TIMEOUT)

    @_metric_patch_exceptions.count_exceptions()
    @_metric_patch_duration.time()
    def patch(self, tenants, patch_body):
        url = self.get_url(AS3_DECLARE_PATH)
        return super(AS3RestClient, self).patch(url, json=patch_body)

    @_metric_delete_exceptions.count_exceptions()
    @_metric_delete_duration.time()
    def delete(self, tenants):
        if not tenants:
            raise exceptions.DeleteAllTenenatsException()

        url = '{}/{}'.format(self.get_url(AS3_DECLARE_PATH), ','.join(tenants))
        return super(AS3RestClient, self).delete(url)

    def info(self):
        info = self.get(AS3_INFO_PATH)
        info.raise_for_status()
        return dict(device=self.hostname, **info.json())


class AS3ExternalContainerRestClient(AS3RestClient):
    """ AS3 rest client that supports external containerized AS3 docker appliances. PATCH/DELETE requests
        are proxied via POST. iControlRest calls are directly called against the backend devices.

        See: https://clouddocs.f5.com/products/extensions/f5-appsvcs-extension/latest/userguide/as3-container.html
    """
    def __init__(self, bigip_url, as3_url, verify=True, auth=None, async_mode=False):
        self.as3_url = parse.urlsplit(as3_url, allow_fragments=False)
        super(AS3ExternalContainerRestClient, self).__init__(bigip_url, verify, auth, async_mode)

    def get_url(self, url):
        """ Override host for AS3 declarations. """
        if url.startswith(AS3_PATH):
            # derive external as3 container url
            url_tuple = parse.SplitResult(
                scheme=self.as3_url.scheme, netloc=self.as3_url.netloc,
                path=url, query='', fragment='')
            return parse.urlunsplit(url_tuple)
        else:
            # derive regular bigip url
            return super(AS3ExternalContainerRestClient, self).get_url(url)

    def post(self, tenants, payload):
        if isinstance(payload, AS3):
            payload.set_bigip_target_host(self.hostname)
            if isinstance(self.auth, bigip_auth.BigIPTokenAuth):
                payload.set_target_tokens({bigip_auth.BIGIP_TOKEN_HEADER: self.auth.token})
            elif isinstance(self.auth, bigip_auth.BigIPBasicAuth):
                payload.set_target_username(self.auth.username)
                payload.set_target_passphrase(self.auth.password)
        return super(AS3ExternalContainerRestClient, self).post(tenants, payload)

    def patch(self, tenants, patch_body):
        # Patch is realized through post with action=patch
        payload = AS3(action='patch', patchBody=patch_body)
        return self.post(tenants or [], payload)

    def delete(self, tenants):
        # Delete is realized through post with action=delete
        if not tenants:
            raise exceptions.DeleteAllTenenatsException()

        payload = AS3(action='remove')
        return self.post(tenants, payload)
