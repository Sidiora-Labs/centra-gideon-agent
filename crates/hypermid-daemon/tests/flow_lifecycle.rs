use std::{sync::Arc, thread, time::Duration, time::Instant};

use hypermid_daemon::{
    cancellation::RouteKey,
    connection::{ConnectionFlow, ConnectionFlowError, TerminalDecision},
    flow::{Admission, RouteFlowConfig},
};
use hypermid_protocol::{Envelope, Id, MessageKind, PROTOCOL};
use hypermid_transport::TerminalKind;

fn route(number: u64) -> RouteKey {
    RouteKey {
        route_id: format!("route-{number}"),
        route_epoch: 1,
    }
}

fn request(route: &RouteKey, id: &str, deadline_ms: u64) -> Envelope {
    Envelope {
        protocol: PROTOCOL.into(),
        kind: MessageKind::Request,
        message_id: Id::new(id).unwrap(),
        sequence: 1,
        reply_to: None,
        route_id: Some(Id::new(&route.route_id).unwrap()),
        route_epoch: Some(route.route_epoch),
        operation: Some("tool.run".into()),
        scope: None,
        trace: None,
        deadline_ms: Some(deadline_ms),
        payload: None,
        error: None,
    }
}

fn config() -> RouteFlowConfig {
    RouteFlowConfig {
        request_credits: 1,
        byte_credits: 64,
        max_queued_requests: 1,
        max_queued_bytes: 64,
    }
}

#[test]
fn exhausted_route_is_isolated_and_control_remains_responsive() {
    let connection = ConnectionFlow::new(1, 16).unwrap();
    let first_route = route(1);
    let second_route = route(2);
    connection
        .register_route(first_route.clone(), config())
        .unwrap();
    connection
        .register_route(second_route.clone(), config())
        .unwrap();
    let now = Instant::now();
    assert_eq!(
        connection
            .admit_request(&request(&first_route, "request-1", 2_000), 32, 1_000, now)
            .unwrap()
            .disposition,
        Admission::Admitted
    );
    assert_eq!(
        connection
            .admit_request(&request(&first_route, "request-2", 2_000), 32, 1_000, now)
            .unwrap()
            .disposition,
        Admission::Queued
    );
    assert!(matches!(
        connection.admit_request(&request(&first_route, "request-3", 2_000), 32, 1_000, now),
        Err(ConnectionFlowError::Flow(_))
    ));
    assert_eq!(
        connection
            .admit_request(&request(&second_route, "request-4", 2_000), 32, 1_000, now)
            .unwrap()
            .disposition,
        Admission::Admitted
    );

    let mut ping = request(&first_route, "control-1", 2_000);
    ping.kind = MessageKind::Ping;
    ping.route_id = None;
    ping.route_epoch = None;
    ping.operation = None;
    connection.enqueue_control(ping).unwrap();
    assert_eq!(
        connection.pop_control().unwrap().unwrap().kind,
        MessageKind::Ping
    );

    assert!(matches!(
        connection.cancel(&Id::new("request-2").unwrap()).unwrap(),
        TerminalDecision::Won(_)
    ));
    assert_eq!(connection.route_counts(&first_route).unwrap(), (1, 0));
}

#[test]
fn terminal_race_releases_credit_once_and_deadline_never_extends() {
    let connection = Arc::new(ConnectionFlow::new(2, 16).unwrap());
    let route = route(9);
    connection.register_route(route.clone(), config()).unwrap();
    let start = Instant::now();
    let admission = connection
        .admit_request(&request(&route, "race-request", 1_050), 32, 1_000, start)
        .unwrap();
    assert_eq!(admission.deadline.wire_deadline_ms(), 1_050);

    let cancel_flow = Arc::clone(&connection);
    let response_flow = Arc::clone(&connection);
    let id = Id::new("race-request").unwrap();
    let cancel_id = id.clone();
    let response_id = id.clone();
    let cancel = thread::spawn(move || cancel_flow.cancel(&cancel_id).unwrap());
    let response = thread::spawn(move || {
        response_flow
            .settle(&response_id, TerminalKind::Response)
            .unwrap()
    });
    let decisions = [cancel.join().unwrap(), response.join().unwrap()];
    assert_eq!(
        decisions
            .iter()
            .filter(|decision| matches!(decision, TerminalDecision::Won(_)))
            .count(),
        1
    );
    assert_eq!(connection.route_available(&route).unwrap().0, 1);
    assert_eq!(connection.route_counts(&route).unwrap(), (0, 0));

    connection
        .admit_request(
            &request(&route, "deadline-request", 2_050),
            16,
            2_000,
            start,
        )
        .unwrap();
    assert_eq!(
        connection
            .expire_at(start + Duration::from_millis(49))
            .unwrap()
            .len(),
        0
    );
    let expired = connection
        .expire_at(start + Duration::from_millis(50))
        .unwrap();
    assert_eq!(expired.len(), 1);
    assert_eq!(expired[0].terminal, TerminalKind::TimedOut);
    assert_eq!(connection.route_available(&route).unwrap().0, 1);
}
