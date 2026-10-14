# Cloud integration policy and validation

<!-- compatibility-forwarder -->

This page preserves legacy C-* bookmarks. Current HA acceptance criteria and
validation gaps live in the [management inventory](progress.md); product
decisions live in [feature scope](../cloud-scope.md). Client method coverage
remains in the linked client API inventory. This lookup table is not a second
status ledger.

## Previous cloud tracker IDs

These anchors preserve links to the former method and implementation ledger. Raw
API details and statuses now live in the client inventory; HA behavior remains
in the management map.

| Former ID | Current location |
| --- | --- |
| <a id="c-i01"></a>**C-I01** | [API-AUTH-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-auth-01), [API-AUTH-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-auth-02) |
| <a id="c-i02"></a>**C-I02** | [API-DEVICE-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-01) |
| <a id="c-i03"></a>**C-I03** | [API-DEVICE-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-02), [API-READ-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-12) |
| <a id="c-i04"></a>**C-I04** | [API-READ-13](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-13) |
| <a id="c-i05"></a>**C-I05** | [API-WRITE-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-01), [API-WRITE-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-02), [API-WRITE-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-03), [API-WRITE-04](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-04) |
| <a id="c-i06"></a>**C-I06** | [API-WRITE-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-08), [API-WRITE-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-09), [API-WRITE-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-10), [API-WRITE-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-11) |
| <a id="c-i07"></a>**C-I07** | [API-WRITE-06](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-06), [API-WRITE-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-07), [API-WRITE-30](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-30) |
| <a id="c-i08"></a>**C-I08** | [API-WRITE-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-12), [API-WRITE-13](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-13), [API-WRITE-14](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-14) |
| <a id="c-i09"></a>**C-I09** | [API-READ-19](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-19), [API-WRITE-20](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-20) |
| <a id="c-i10"></a>**C-I10** | [API-READ-15](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-15) |
| <a id="c-i11"></a>**C-I11** | [API-READ-17](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-17) |
| <a id="c-i12"></a>**C-I12** | [API-SCHED-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-01), [API-SCHED-05](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-05), [API-SCHED-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-07) |
| <a id="c-i13"></a>**C-I13** | [API-SCHED-13](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-13), [API-SCHED-14](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-14), [API-SCHED-15](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-15) |
| <a id="c-i14"></a>**C-I14** | [API-READ-22](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-22), [API-WRITE-22](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-22) |
| <a id="c-i15"></a>**C-I15** | [API-DEVICE-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-03), [API-DEVICE-05](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-05) |
| <a id="c-i16"></a>**C-I16** | [API-DEVICE-04](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-04) |
| <a id="c-i17"></a>**C-I17** | [API-WRITE-21](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-21) |
| <a id="c-i18"></a>**C-I18** | [API-READ-04](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-04), [API-READ-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-08), [API-READ-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-09), [API-READ-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-10), [API-READ-21](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-21) |
| <a id="c-a01"></a><a id="c-a01-criteria"></a>**C-A01** | [API-WRITE-20](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-20); [MGT-QUALITY-04](progress.md#mgt-quality-04) |
| <a id="c-a02"></a><a id="c-a02-criteria"></a>**C-A02** | [API-READ-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-09); [MGT-STATE-04](progress.md#mgt-state-04) |
| <a id="c-a03"></a><a id="c-a03-criteria"></a>**C-A03** | [MGT-STATE-03](progress.md#mgt-state-03) |
| <a id="c-a04"></a><a id="c-a04-criteria"></a>**C-A04** | [MGT-COMMAND-04](progress.md#mgt-command-04) |
| <a id="c-a05"></a><a id="c-a05-criteria"></a>**C-A05** | [MGT-COMMAND-04](progress.md#mgt-command-04), [MGT-COMMAND-05](progress.md#mgt-command-05), [MGT-QUALITY-03](progress.md#mgt-quality-03) |
| <a id="c-a06"></a><a id="c-a06-criteria"></a>**C-A06** | [API-SCHED-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-01), [API-SCHED-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-02), [API-SCHED-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-03), [API-SCHED-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-08), [API-SCHED-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-09), [API-SCHED-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-10), [API-SCHED-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-11); [MGT-LIFECYCLE-08](progress.md#mgt-lifecycle-08) |
| <a id="c-a07"></a><a id="c-a07-criteria"></a>**C-A07** | [MGT-QUALITY-06](progress.md#mgt-quality-06) |
| <a id="c-a08"></a><a id="c-a08-criteria"></a>**C-A08** | [API-SCHED-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-01), [API-SCHED-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-02), [API-SCHED-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-03) |
| <a id="c-p01"></a><a id="c-p01-criteria"></a>**C-P01** | [API-READ-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-03) |
| <a id="c-p02"></a><a id="c-p02-criteria"></a>**C-P02** | [API-READ-01](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-01), [API-READ-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-03), [API-READ-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-09); [MGT-STATE-01](progress.md#mgt-state-01), [MGT-STATE-02](progress.md#mgt-state-02), [MGT-STATE-03](progress.md#mgt-state-03), [MGT-STATE-04](progress.md#mgt-state-04) |
| <a id="c-p03"></a><a id="c-p03-criteria"></a>**C-P03** | [Cloud protocol routes](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#scope-and-evidence); [MGT-COMMAND-05](progress.md#mgt-command-05) |
| <a id="c-p04"></a><a id="c-p04-criteria"></a>**C-P04** | [API-WRITE-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-08), [API-WRITE-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-09), [API-WRITE-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-10), [API-WRITE-12](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-12), [API-WRITE-13](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-13), [API-WRITE-14](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-14); [MGT-COMMAND-06](progress.md#mgt-command-06) |
| <a id="c-p05"></a><a id="c-p05-criteria"></a>**C-P05** | [API-SCHED-02](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-02), [API-SCHED-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-08), [API-SCHED-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-09); [MGT-LIFECYCLE-08](progress.md#mgt-lifecycle-08) |
| <a id="c-p06"></a><a id="c-p06-criteria"></a>**C-P06** | [API-SCHED-03](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-03), [API-SCHED-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-10), [API-SCHED-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-11); [MGT-LIFECYCLE-08](progress.md#mgt-lifecycle-08) |
| <a id="c-p08"></a><a id="c-p08-criteria"></a>**C-P08** | [API-READ-27](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-27) |
| <a id="c-p09"></a><a id="c-p09-criteria"></a>**C-P09** | [API-READ-16](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-16) |
| <a id="c-p10"></a><a id="c-p10-criteria"></a>**C-P10** | [API-READ-18](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-18) |
| <a id="c-p11"></a><a id="c-p11-criteria"></a>**C-P11** | [API-READ-21](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-21) |
| <a id="c-p12"></a><a id="c-p12-criteria"></a>**C-P12** | [API-READ-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-10) |
| <a id="c-p13"></a><a id="c-p13-criteria"></a>**C-P13** | [API-READ-11](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-read-11), [API-WRITE-15](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-15), [API-WRITE-16](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-16), [API-WRITE-17](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-17), [API-WRITE-18](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-write-18); [MGT-COMMAND-07](progress.md#mgt-command-07), [MGT-COMMAND-08](progress.md#mgt-command-08), [MGT-QUALITY-02](progress.md#mgt-quality-02) |
| <a id="c-p14"></a><a id="c-p14-criteria"></a>**C-P14** | [API-DEVICE-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-07), [API-DEVICE-08](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-08), [API-DEVICE-09](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-09), [API-DEVICE-10](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-device-10), [API-SETUP-05](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-setup-05), [API-SETUP-06](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-setup-06), [API-SETUP-07](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-setup-07); [MGT-WORKFLOW-06](progress.md#mgt-workflow-06) |
| **C-P07** | [In-place schedule editing](#c-p07) is a Home Assistant scope decision; raw method status is [API-SCHED-06](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md#api-sched-06). |

## HA scope decisions

| Former ID | Authoritative decision |
| --- | --- |
| <a id="c-o01"></a>**C-O01** | [Scope decision](../cloud-scope.md#templates) |
| <a id="c-o02"></a>**C-O02** | [Scope decision](../cloud-scope.md#schedules) |
| <a id="c-o03"></a>**C-O03** | [Scope decision](../cloud-scope.md#shortcuts) |
| <a id="c-o04"></a>**C-O04** | [Scope decision](../cloud-scope.md#zones-and-energy) |
| <a id="c-o05"></a>**C-O05** | [Scope decision](../cloud-scope.md#zones-and-energy) |
| <a id="c-o06"></a>**C-O06** | [Scope decision](../cloud-scope.md#zones-and-energy) |
| <a id="c-o07"></a>**C-O07** | [Scope decision](../cloud-scope.md#geofences) |
| <a id="c-o08"></a>**C-O08** | [Scope decision](../cloud-scope.md#notifications) |
| <a id="c-o09"></a>**C-O09** | [Scope decision](../cloud-scope.md#notifications) |
| <a id="c-o10"></a>**C-O10** | [Scope decision](../cloud-scope.md#account-administration) |
| <a id="c-o11"></a>**C-O11** | [Scope decision](../cloud-scope.md#names-and-time-zones); [eco / animation settings](../cloud-scope.md#app-settings) |
| <a id="c-o12"></a>**C-O12** | [Scope decision](../cloud-scope.md#demand-response); [eco / animation settings](../cloud-scope.md#app-settings) |
| <a id="c-o13"></a>**C-O13** | [Scope decision](../cloud-scope.md#app-services) |
| <a id="c-o14"></a>**C-O14** | [Scope decision](../cloud-scope.md#app-services) |
| <a id="c-o15"></a>**C-O15** | [Scope decision](../cloud-scope.md#obsolete-provisioning) |
| <a id="c-o16"></a>**C-O16** | [Scope decision](../cloud-scope.md#discovery) |
| <a id="c-o17"></a>**C-O17** | [Scope decision](../cloud-scope.md#electrical-data) |
| <a id="c-o18"></a>**C-O18** | [Scope decision](../cloud-scope.md#cloud-transports) |
| <a id="c-p07"></a><a id="c-p07-criteria"></a>**C-P07** | [Schedule editor boundary](../cloud-scope.md#schedules) |

## Validation limits

See [research and hardware limits](progress.md#research-and-hardware-limits),
[command application](progress.md#mgt-quality-03), and
[firmware installation](progress.md#mgt-quality-04).

## Maintenance

Preserve these legacy anchors. Add new HA criteria to the MGT-* inventory and
update product decisions in feature scope. See [documentation maintenance](../maintenance.md).
