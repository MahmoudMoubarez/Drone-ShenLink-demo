"""Post-run evidence, using the shared monotonic clock of this one-host lab.

Arrival gaps are NOT network handover latency or browser paint measurements.
Only verified, mutation-free windows are attributed to a path configuration.
"""
from bisect import bisect_left, bisect_right


def analyze(events, frames, sensors, commands, executions, end_ns):
    events = sorted(events, key=lambda row: row['verified_ns'])
    fresh, seen = [], set()
    for row in sorted(frames, key=lambda row: row['received_ns']):
        key = (row['session'], row['id'])
        if row.get('delivery_kind') != 'quality_upgrade' and key not in seen:
            seen.add(key)
            fresh.append(row)
    times = [row['received_ns'] for row in fresh]
    sensors = sorted(sensors, key=lambda row: row['received_ns'])
    commands = sorted(commands, key=lambda row: row['sent_ns'])
    command_times = [row['sent_ns'] for row in commands]
    execution_by_id = {row['id']: row for row in executions}
    feedback = {}
    for row in sensors:
        key = (row.get('command_id'), row['axis'])
        feedback.setdefault(key, row)
    axis_rows = {axis: [row for row in sensors if row['axis'] == axis]
                 for axis in ('yaw', 'pitch')}
    axis_times = {axis: [row['received_ns'] for row in rows]
                  for axis, rows in axis_rows.items()}

    def window(start, end):
        lo, hi = bisect_right(times, start), bisect_left(times, end)
        rows = fresh[lo:hi]
        previous = fresh[lo-1] if lo else None
        # Edges measure time without a NEW arrival inside this window. They
        # intentionally do not assume a future frame will ever arrive.
        points = [start] + [row['received_ns'] for row in rows] + [end]
        sensor_result = {}
        for axis in ('yaw', 'pitch'):
            ts = axis_times[axis]
            a, b = bisect_right(ts, start), bisect_left(ts, end)
            latest = ts[b-1] if b else None
            pts = [start] + ts[a:b] + [end]
            sensor_result[axis] = dict(samples=b-a,
                end_age_ms=(end-latest)/1e6 if latest is not None else None,
                max_no_arrival_interval_ms=max((y-x)/1e6 for x, y in zip(pts, pts[1:])))
        selected = commands[bisect_left(command_times, start):bisect_left(command_times, end)]
        receipts = {}
        command_results = []
        for row in selected:
            status = row['receipt']
            receipts[status] = receipts.get(status, 0)+1
            robot = execution_by_id.get(row['id'])
            accepted = robot is not None and robot['outcome'] == 'applied'
            executed = robot.get('executed_ns') if accepted else None
            command_results.append(dict(id=row['id'], sent_ns=row['sent_ns'], receipt=status,
                robot_outcome=robot['outcome'] if robot else 'unobserved',
                robot_received_ns=robot['received_ns'] if robot else None,
                executed_ns=executed,
                send_to_apply_ms=(executed-row['sent_ns'])/1e6 if executed is not None else None,
                applied_within_window=accepted and start <= executed < end,
                sensor_confirmation_ns={axis: feedback[(row['id'],axis)]['received_ns']
                    for axis in ('yaw','pitch') if (row['id'],axis) in feedback}))
        return dict(new_frames=len(rows), delivered_paths=sorted({row['path'] for row in rows}),
            sessions=sorted({row['session'] for row in rows}),
            first_new_frame=rows[0] if rows else None, last_frame_before_verified_cut=previous,
            max_no_new_frame_interval_ms=max((y-x)/1e6 for x, y in zip(points, points[1:])),
            sensors=sensor_result, commands_sent=len(selected), receipts=receipts,
            commands=command_results), rows, previous

    phases, cuts = [], []
    up = {path: True for path in 'abc'}
    for index, event in enumerate(events):
        up[event['path']] = event['up']
        start = event['verified_ns']
        # Mutation can affect traffic BEFORE its readback completes. Do not
        # attribute this uncertain period to either verified configuration.
        end = min(end_ns, events[index+1]['started_ns']) if index+1 < len(events) else end_ns
        if end < start:
            raise ValueError('Overlapping mutation timestamps or incomplete observation clock')
        enabled = sorted(path for path, available in up.items() if available)
        kind = ('all_paths_outage' if not enabled else 'single_path_cut' if len(enabled) == 2
                else 'two_paths_cut' if len(enabled) == 1 else 'all_paths_enabled')
        measurement, rows, previous = window(start, end)
        phase = dict(event_id=event.get('event_id', index+1), category=kind,
            enabled_paths=enabled, start_ns=start, end_ns=end,
            duration_ms=(end-start)/1e6, ended_by='next_mutation' if index+1 < len(events) else 'recording_end',
            **measurement)
        phases.append(phase)
        if event['up']:
            continue
        restore = next((row for row in events[index+1:]
                        if row['path'] == event['path'] and row['up']), None)
        alternative = next((row for row in rows if row['path'] in enabled), None)
        cycle_end = min(end_ns, restore['verified_ns']) if restore else end_ns
        cycle_measurement, _, _ = window(event['started_ns'], cycle_end)
        # Retain the whole experiment, including mutation periods, separately
        # from stable-state attribution. Flag overlapping all-path outages.
        cycle_up = dict(up)
        minimum_enabled = len(enabled)
        intervening = []
        for later_index,later in enumerate(events[index+1:],index+1):
            if later['started_ns'] >= cycle_end:
                break
            intervening.append(later.get('event_id',later_index+1))
            if later['verified_ns'] <= cycle_end:
                cycle_up[later['path']] = later['up']
                minimum_enabled = min(minimum_enabled, sum(cycle_up.values()))
        cuts.append(dict(event_id=phase['event_id'], path=event['path'],
            origin=event.get('origin', 'unknown'), pulse_id=event.get('pulse_id'),
            selection=event.get('selection'), category=kind,
            cut_started_ns=event['started_ns'], cut_verified_ns=start,
            restore_started_ns=restore['started_ns'] if restore else None,
            restore_verified_ns=restore['verified_ns'] if restore else None,
            verified_down_hold_ms=(restore['started_ns']-start)/1e6 if restore else None,
            restored=restore is not None,
            evidence_window_end_ns=end,
            cut_path_was_last_delivered=previous['path'] == event['path'] if previous else None,
            last_frame_before_verified_cut=previous, first_alternative_frame=alternative,
            alternative_delivery_observed=alternative is not None,
            verified_cut_to_alternative_ms=(alternative['received_ns']-start)/1e6 if alternative else None,
            last_to_alternative_arrival_gap_ms=(alternative['received_ns']-previous['received_ns'])/1e6
                if alternative and previous else None,
            max_no_new_frame_interval_ms=phase['max_no_new_frame_interval_ms'],
            commands_sent=phase['commands_sent'], sensors=phase['sensors'],
            cycle=dict(start_ns=event['started_ns'],end_ns=cycle_end,
                includes_mutation_periods=True,minimum_verified_enabled_paths=minimum_enabled,
                contains_all_paths_outage=minimum_enabled==0,
                intervening_event_ids=intervening,**cycle_measurement)))
    single = [row for row in cuts if row['category'] == 'single_path_cut']
    return dict(schema_version=1,
        scope='Same-host SDK arrivals and robot command application; kernel netem scenario, not physical radios or browser paint.',
        measurement_note='Arrival gaps are not handover latency. Execution means the robot applied a control, not that a motion target was reached. Mutation periods are excluded from configuration windows; frames already in flight can still arrive.',
        fresh_frame_count=len(fresh), ignored_frame_callbacks=len(frames)-len(fresh),
        cut_count=len(cuts), single_path_cut_count=len(single),
        single_path_alternative_observed=sum(row['alternative_delivery_observed'] for row in single),
        all_paths_outage_count=sum(row['category'] == 'all_paths_outage' for row in phases),
        max_single_path_no_new_frame_interval_ms=max((row['max_no_new_frame_interval_ms'] for row in phases
            if row['category'] == 'single_path_cut'), default=None),
        max_isolated_single_path_cycle_no_new_frame_interval_ms=max((row['cycle']['max_no_new_frame_interval_ms']
            for row in single if row['cycle']['minimum_verified_enabled_paths']==2),default=None),
        cuts=cuts, phases=phases)
