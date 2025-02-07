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


class AS3Exception(Exception):
    pass


class RetryException(Exception):
    pass


class FailoverException(Exception):
    pass


class IControlRestException(Exception):
    pass


class PolicyHasNoRules(AS3Exception):
    pass


class NoActionFoundForPolicy(AS3Exception):
    pass


class CompareTypeNotSupported(AS3Exception):
    pass


class PolicyTypeNotSupported(AS3Exception):
    pass


class PolicyActionNotSupported(AS3Exception):
    pass


class MonitorDeletionException(AS3Exception):
    def __init__(self, tenant, application, monitor):
        super(MonitorDeletionException).__init__()
        self.tenant = tenant
        self.application = application
        self.monitor = monitor


class DeleteAllTenantsException(Exception):
    def __init__(self):
        super(DeleteAllTenantsException).__init__()
        self.message = 'Delete called without tenant, would wipe all AS3 Declaration, ignoring.'
