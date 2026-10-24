package com.weaknet.app;

/**
 * 端口对 -> 快查槽位下标。纯函数、无 Android 依赖，可单测穷举验证。
 *
 * 背景：VpnNetService 为每个 TCP/UDP 包查会话表，原实现把 IP 字符串化再拼 key，
 * 每包十几次分配。改为「端口对 -> 数组槽位」的直接映射后换热路径零分配。
 *
 * 槽位只有 65536 个，而端口对是 32 位组合，必须折叠：
 *  - 不能直接用 32 位组合：srcPort>=0x8000 时 (src&0xFFFF)<<16 溢出成负数，
 *    srcPort 较小时组合值也远超数组长度，两者都会 ArrayIndexOutOfBounds。
 *  - & 0xFFFF 折叠后低 16 位即 dstPort，同槽冲突由调用方 sameFlow() 核对 IP/端口，
 *    核不上回落字符串 key 慢路径，正确性不受影响（只影响命中率）。
 */
final class PortIdx {
    private PortIdx() {}

    /** (srcPort, dstPort) -> [0, 65535] 槽位下标 */
    static int of(int srcPort, int dstPort) {
        return (((srcPort & 0xFFFF) << 16) | (dstPort & 0xFFFF)) & 0xFFFF;
    }
}
