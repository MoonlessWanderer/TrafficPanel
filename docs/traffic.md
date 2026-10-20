# Traffic calculation

Traffic is the difference of cumulative operating-system counters, not a precomputed total.

- Interval RX is the current RX counter minus the previous RX counter, when the counter did not move backward.
- Interval TX uses the same rule.
- Speed is that difference divided by the real seconds between the two samples.
- A node total is the sum of interfaces other than `lo` in the same interval.
- Average speed across nodes is the mean of each node's average speed. It is not the grand total divided by the sample count again.

If the gap between two samples is longer than the allowed sample gap in panel settings, that interval is a gap. No zero-traffic row is stored for it, and it is left out of totals.

If a counter becomes smaller, that is treated as a reset, usually after a reboot. A negative value is not stored. Traffic after the counter returns to zero is estimated as the new counter value for that interval.

A sample with the same collection time is not calculated again.

## Units

- Traffic volume in the UI uses decimal steps: KB, MB, GB, TB (base 1000).
- Speed uses bits per second: Kbps, Mbps, Gbps.
- RAM and disk use base 1024.
- Storage is always bytes.

## Difference from a host bill

Operating-system counters are not the same as a provider invoice. The host may meter traffic in the hypervisor, before it reaches this interface, or with rules that exclude internal traffic. This panel does not estimate sample gaps or counter resets beyond the rules above. A sum of several interfaces can include internal paths. The panel shows usage observed on the operating system itself.
