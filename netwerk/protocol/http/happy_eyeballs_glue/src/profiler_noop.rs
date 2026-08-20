/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

pub(crate) type FlowIdentifier = u64;

pub(crate) fn initial_flow_id() -> FlowIdentifier {
    0
}

pub(crate) fn flow_id_from_ptr<T>(ptr: *const T) -> FlowIdentifier {
    ptr as usize as u64
}

pub(crate) struct Profiler;

impl Profiler {
    pub(crate) fn new(
        _flow_id: FlowIdentifier,
        _origin: &str,
        _network_config: &happy_eyeballs::NetworkConfig,
    ) -> Self {
        Self
    }

    pub(crate) fn set_flow_id(&mut self, _flow_id: FlowIdentifier) {}

    pub(crate) fn dns_query_started(
        &mut self,
        _id: happy_eyeballs::Id,
        _record_type: happy_eyeballs::DnsRecordType,
    ) {
    }

    pub(crate) fn dns_response(
        &mut self,
        _id: happy_eyeballs::Id,
        _addrs: &[impl std::fmt::Display],
    ) {
    }

    pub(crate) fn dns_response_https(
        &mut self,
        _id: happy_eyeballs::Id,
        _infos: &[happy_eyeballs::ServiceInfo],
    ) {
    }

    pub(crate) fn connection_attempt_started(
        &mut self,
        _id: happy_eyeballs::Id,
        _endpoint: &happy_eyeballs::Endpoint,
    ) {
    }

    pub(crate) fn connection_cancelled(&mut self, _id: happy_eyeballs::Id) {}

    pub(crate) fn connection_result(&mut self, _id: happy_eyeballs::Id, _succeeded: bool) {}
}
