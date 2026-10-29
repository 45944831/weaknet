package com.weaknet.app;

import java.util.LinkedHashMap;
import java.util.Map;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 自动生成：weaknet/tools/gen_spec.py ← spec/spec.json（hash 2047b879）。请勿手改。
 *
 * 预置场景 / 参数规格 / 文案 / 组合规则的唯一来源是 spec.json；
 * 改动规格后执行 python tools/gen_spec.py 重新生成本类。
 */
public final class SpecGen {
    private SpecGen() {
    }

    public static final String SPEC_HASH = "2047b879";
    public static final int SHARE_VER = 2;
    public static final int PIN_LEN = 4;
    public static final int INBOX_MAX = 20;
    public static final int SHARE_MAX_STEPS = 5;
    public static final int COMBO_MAX_STEPS = 5;
    public static final int COMBO_MAX_MINUTES = 600;
    public static final int COMBO_MAX_SECONDS = 59;
    public static final int COMBO_EXAMPLE_COUNT = 5;
    public static final String COMBO_DOC = "\u5f31\u7f51\u7ec4\u5408 = \u628a\u51e0\u652f\u573a\u666f\u6309\u65f6\u957f\u6392\u6210\u4e00\u6761\u94fe\uff0c\u9009\u4e2d\u540e\u81ea\u52a8\u4e00\u6bb5\u6bb5\u64ad\u8fc7\u53bb\u3002\n\u00b7 \u987a\u5e8f\u64ad\u653e\uff1a\u4e00\u6bb5\u8dd1\u5b8c\u81ea\u52a8\u5207\u4e0b\u4e00\u6bb5\uff0c\u4e2d\u95f4\u4e0d\u7528\u4f60\u64cd\u4f5c\u3002\n\u00b7 \u53ef\u5faa\u73af\uff1a\u672b\u5c3e\u518d\u56de\u5230\u7b2c\u4e00\u6bb5\uff0c\u4e00\u76f4\u5faa\u73af\u4e0b\u53bb\uff08\u4e0d\u52fe\u5c31\u64ad\u5b8c\u4e00\u904d\u505c\u4f4f\uff09\u3002\n\u00b7 \u6700\u591a 5 \u6bb5\uff1a\u518d\u591a\u5c31\u4e0d\u597d\u5728\u754c\u9762\u4e0a\u6309\u987a\u5e8f\u6838\u5bf9\u4e86\u3002\n\u00b7 \u7ec4\u5408\u91cc\u4e0d\u80fd\u518d\u5957\u7ec4\u5408\uff1a\u6bb5\u53ea\u80fd\u9009\u9884\u8bbe\u6216\u81ea\u5b9a\u4e49\u573a\u666f\u3002\n\u00b7 \u5f31\u7f51\u6ca1\u5f00\u7740\u65f6\u70b9\u7ec4\u5408\u53ea\u300c\u9009\u4e2d\u300d\uff0c\u4e0d\u4f1a\u66ff\u4f60\u5077\u5077\u5f00\u59cb\u9650\u901f\uff1b\u5f00\u7740\u624d\u4f1a\u7acb\u523b\u64ad\u3002\u001f\u901a\u52e4\u8def\u4e0a\n\u4ece\u6709\u4fe1\u53f7\u8d70\u8fdb\u4fe1\u53f7\u5dee\u7684\u5730\u65b9\uff0c\u518d\u5f7b\u5e95\u6ca1\u4fe1\u53f7\uff0c\u6700\u540e\u6062\u590d\u3002\n\u590d\u73b0\u300c\u5730\u94c1\u8fdb\u96a7\u9053\u300d\u8fd9\u7c7b\u771f\u5b9e\u94fe\u8def\uff1a\u5148\u6b63\u5e38\u52a0\u8f7d\uff0c\u8d70\u5230\u5f31\u8986\u76d6\u533a\u5f00\u59cb\u5361\uff0c\u8fdb\u96a7\u9053\u540e\u8bf7\u6c42\u6302\u4f4f\uff0c\u51fa\u7ad9\u540e\u6062\u590d\u3002\u9002\u5408\u6d4b\u5f31\u7f51\u4e0b\u7684\u52a0\u8f7d\u4e2d\u72b6\u6001\u4e0e\u6062\u590d\u903b\u8f91\u3002\n5G \u826f\u597d 30 \u79d2 \u2192 4G \u5f31\u7f51 1 \u5206 \u2192 \u5b8c\u5168\u65ad\u7f51 30 \u79d2 \u2192 5G \u826f\u597d 30 \u79d2\u001f\u62a2\u8d2d\u79d2\u6740\n\u5e73\u65f6\u6d41\u7545\uff0c\u4e00\u5230\u9ad8\u5cf0\u5c31\u62a2\u4e0d\u5230\uff0c\u5931\u8d25\u540e\u5feb\u901f\u91cd\u8bd5\u3002\n\u9ad8\u5e76\u53d1\u4e0b\u670d\u52a1\u7aef\u9650\u6d41\u6216\u7f51\u7edc\u62e5\u585e\u7684\u5178\u578b\u8868\u73b0\uff1a\u8bf7\u6c42\u53d8\u6162\u3001\u90e8\u5206\u76f4\u63a5\u5931\u8d25\u3002\u9002\u5408\u9a8c\u8bc1 App \u7684\u5931\u8d25\u63d0\u793a\u3001\u91cd\u8bd5\u4e0e\u964d\u7ea7\u8def\u5f84\u3002\n5G \u826f\u597d 20 \u79d2 \u2192 \u5f31 Wi-Fi 1 \u5206 \u2192 \u9ed1\u6d1e\uff08\u65e0\u54cd\u5e94\uff0915 \u79d2 \u2192 \u5f31 Wi-Fi 1 \u5206\u001f\u7f51\u7edc\u6296\u52a8\n\u4fe1\u53f7\u65f6\u597d\u65f6\u574f\uff0c\u5ef6\u8fdf\u5ffd\u5927\u5ffd\u5c0f\u3002\n\u6a21\u62df\u7535\u68af\u3001\u96a7\u9053\u53e3\u3001\u62e5\u6324\u573a\u6240\u7684\u4fe1\u53f7\u6296\u52a8\u3002\u9002\u5408\u6d4b\u5b9e\u65f6\u97f3\u89c6\u9891\u3001\u8bed\u97f3\u901a\u8bdd\u3001\u5728\u7ebf\u534f\u4f5c\u8fd9\u7c7b\u5bf9\u5ef6\u8fdf\u654f\u611f\u7684\u94fe\u8def\u3002\n5G \u826f\u597d 20 \u79d2 \u2192 \u5f31 5G 20 \u79d2 \u2192 5G \u826f\u597d 20 \u79d2 \u2192 \u5f31 5G 20 \u79d2\uff08\u5faa\u73af\uff09\u001f\u5b9a\u65f6\u65ad\u7f51\n\u6bcf\u9694\u4e00\u4f1a\u513f\u65ad\u4e00\u6b21\uff0c\u8fc7\u4e00\u4f1a\u513f\u81ea\u5df1\u6062\u590d\u3002\n\u6a21\u62df\u5468\u671f\u6027\u7f51\u7edc\u4e2d\u65ad\uff08\u5982\u57fa\u7ad9\u5207\u6362\u3001\u5bb6\u5ead\u8def\u7531\u91cd\u542f\uff09\u3002\u9002\u5408\u9a8c\u8bc1\u957f\u8fde\u63a5\u65ad\u4e86\u80fd\u4e0d\u80fd\u81ea\u5df1\u91cd\u8fde\u3001\u5fc3\u8df3\u4e0e\u8d85\u65f6\u903b\u8f91\u3002\n4G \u826f\u597d 1 \u5206 \u2192 \u9ed1\u6d1e\uff08\u65e0\u54cd\u5e94\uff0920 \u79d2\uff08\u5faa\u73af\uff09\u001f\u52a0\u8f7d\u6e10\u53d8\n\u4ece\u6162\u5230\u5feb\u518d\u5230\u6162\uff0c\u6a21\u62df\u5e26\u5bbd\u88ab\u9010\u6b65\u653e\u5f00\u3002\n\u6d4b\u5927\u6587\u4ef6 / \u957f\u5217\u8868\u7684\u6e10\u8fdb\u52a0\u8f7d\u8868\u73b0\uff1a\u9996\u5c4f\u6162\u3001\u4e4b\u540e\u63d0\u901f\u3001\u5feb\u7ed3\u675f\u65f6\u53c8\u6162\u4e0b\u6765\u3002\u9002\u5408\u9a8c\u8bc1\u8fdb\u5ea6\u6761\u4e0e\u5206\u7247\u4e0b\u8f7d\u903b\u8f91\u3002\n\u6781\u6162\uff08100kbps\uff0920 \u79d2 \u2192 4G \u5f31\u7f51 20 \u79d2 \u2192 5G \u826f\u597d 20 \u79d2 \u2192 \u6781\u6162\uff08100kbps\uff0920 \u79d2\u001f\u7ec4\u5408\u64ad\u5230\u4e00\u534a\u53ef\u4ee5\u968f\u65f6\u5207\u8d70\u6216\u6682\u505c\uff1a\u5207\u6362\u540e\u539f\u7ec4\u5408\u505c\u5728\u539f\u5730\uff0c\u4e0d\u4f1a\u5077\u5077\u7ee7\u7eed\u6539\u53c2\u6570\u3002\u001f\u6bcf\u4e00\u6bb5\u90fd\u662f\u5b8c\u6574\u7684\u4e00\u652f\u573a\u666f\uff0c\u6bb5\u4e0e\u6bb5\u4e4b\u95f4\u53c2\u6570\u662f\u6574\u5957\u66ff\u6362\u7684 \u2014\u2014 \u4e0d\u4f1a\u628a\u4e0a\u4e00\u6bb5\u7684\u4e22\u5305\u7387\u5e26\u8fdb\u4e0b\u4e00\u6bb5\u3002\u001f\u5faa\u73af\u7ec4\u5408\u4f1a\u4e00\u76f4\u64ad\u4e0b\u53bb\uff0c\u6d4b\u5b8c\u8bb0\u5f97\u5207\u8d70\u6216\u6682\u505c\uff0c\u522b\u5fd8\u4e86\u5b83\u5728\u540e\u53f0\u6539\u7740\u53c2\u6570\u3002";
    public static final String DEFAULT_PROFILE_NAME = "\u76f4\u8fde\uff08\u4e0d\u9650\u5236\uff09";

    // ---------------------------------------------------------------- 预置场景

    public static final String[] PRESET_KEYS = {"direct", "wifi", "4g", "4g_weak", "5g", "5g_weak", "wifi_weak", "congestion", "satellite", "high_loss", "high_latency", "highspeed_rail", "elevator", "flapping", "blackhole", "dns_fail", "airplane"};

    /** key -> {name, short, cat, note} */
    private static final Map<String, String[]> PRESET_META =
            new LinkedHashMap<String, String[]>();

    static {
        PRESET_META.put("direct", new String[]{"\u76f4\u8fde\uff08\u4e0d\u9650\u5236\uff09", "\u76f4\u8fde", "\u5e38\u89c4\u7f51\u7edc", "\u4e0d\u505a\u4efb\u4f55\u5904\u7406\uff0c\u6d41\u91cf\u539f\u6837\u76f4\u901a\u3002\u7528\u6765\u786e\u8ba4\u300c\u63a5\u7ba1\u672c\u8eab\u6ca1\u95ee\u9898\u300d\uff0c\u4e5f\u662f\u8ddf\u5176\u5b83\u573a\u666f\u5bf9\u6bd4\u7684\u57fa\u7ebf\u3002"});
        PRESET_META.put("wifi", new String[]{"Wi-Fi \u826f\u597d", "Wi-Fi \u826f\u597d", "\u5e38\u89c4\u7f51\u7edc", "\u5bb6\u7528 / \u529e\u516c Wi-Fi \u7684\u5178\u578b\u503c\uff1a\u5ef6\u8fdf\u51e0\u5341\u6beb\u79d2\u3001\u5e26\u5bbd\u51e0\u5341 Mbps\u3002\u529f\u80fd\u9a8c\u8bc1\u548c\u6027\u80fd\u5bf9\u6bd4\u7684\u57fa\u51c6\u73af\u5883\u3002"});
        PRESET_META.put("4g", new String[]{"4G LTE", "4G LTE", "\u5e38\u89c4\u7f51\u7edc", "\u79fb\u52a8\u7f51\u7edc\u5e38\u89c4\u4f53\u9a8c\uff1aRTT 40~60ms\u3001\u4e0b\u884c 20Mbps\u3002\u6a21\u62df\u6237\u5916 / \u5730\u94c1\u91cc\u4fe1\u53f7\u6b63\u5e38\u7684 4G\u3002"});
        PRESET_META.put("4g_weak", new String[]{"4G \u5f31\u7f51\uff08\u8fb9\u7f18/\u5ba4\u5185\uff09", "4G \u5f31\u7f51", "\u5f31\u7f51\u73af\u5883", "\u5730\u4e0b\u8f66\u5e93 / \u57ce\u4e2d\u6751 / \u6f14\u5531\u4f1a\u73b0\u573a\u8fd9\u7c7b\u300c\u6709 4G \u56fe\u6807\u4f46\u901f\u7387\u584c\u4e86\u300d\u7684\u73b0\u573a\u3002\u901f\u7387\u6bd4 5G \u5f31\u8986\u76d6\u518d\u4f4e\u4e00\u6863\uff0c\u4e22\u5305\u4e0e\u5076\u53d1\u65ad\u6d41\u66f4\u660e\u663e \u2014\u2014 4G \u7684\u91cd\u4f20\u6bd4 5G \u6162\u3002"});
        PRESET_META.put("5g", new String[]{"5G\uff08\u826f\u597d\u8986\u76d6\uff09", "5G \u826f\u597d", "\u5e38\u89c4\u7f51\u7edc", "\u7a7a\u53e3\u5ef6\u8fdf\u6bd4 4G \u4f4e\u4e00\u4e2a\u91cf\u7ea7\uff08RTT 10~20ms\uff09\uff0c\u4e0b\u884c 100Mbps\u3002\u80fd\u89e6\u53d1\u300c\u79d2\u5f00\u300d\u7ea7\u4f53\u9a8c\uff0c\u4e5f\u591f\u8dd1\u6ee1\u591a\u6570 App \u7684\u9884\u52a0\u8f7d\u3002"});
        PRESET_META.put("5g_weak", new String[]{"5G \u5f31\u8986\u76d6\uff08\u5ba4\u5185/\u8fb9\u7f18\uff09", "5G \u5f31\u8986\u76d6", "\u5f31\u7f51\u73af\u5883", "\u5ba4\u5185\u6df1\u5904 / \u5c0f\u533a\u8fb9\u7f18 / \u5730\u94c1\u53e3\uff1a\u8fd8\u80fd\u9a7b\u7559\uff0c\u4f46\u901f\u7387\u584c\u5230\u51e0 Mbps\uff0c\u4f34\u968f\u5c11\u91cf\u4e22\u5305\u4e0e\u5076\u53d1\u65ad\u6d41\u3002\u6bd4\u5f31 Wi-Fi \u66f4\u5bb9\u6613\u51fa\u73b0\u300c\u770b\u7740\u6709\u4fe1\u53f7\u3001\u5b9e\u9645\u4f20\u4e0d\u52a8\u300d\u3002"});
        PRESET_META.put("wifi_weak", new String[]{"\u5f31 Wi-Fi\uff08\u9694\u5899/\u8fdc\u7aef\uff09", "\u5f31 Wi-Fi", "\u5f31\u7f51\u73af\u5883", "\u9694\u5899 / \u79bb\u8def\u7531\u5668\u8fdc\uff1a\u4fe1\u53f7\u8fd8\u5728\uff0c\u4f46\u901f\u7387\u4e0e\u7a33\u5b9a\u6027\u4e00\u8d77\u6389\uff0c\u6296\u52a8\u660e\u663e\uff08\u00b160ms\uff09\u3002\u9002\u5408\u590d\u73b0\u300cWi-Fi \u6ee1\u683c\u5374\u4e00\u76f4\u8f6c\u5708\u300d\u3002"});
        PRESET_META.put("congestion", new String[]{"\u7f51\u7edc\u62e5\u585e", "\u7f51\u7edc\u62e5\u585e", "\u5f31\u7f51\u73af\u5883", "\u665a\u9ad8\u5cf0 / \u591a\u4eba\u5171\u4eab\u5e26\u5bbd\uff1a\u5ef6\u8fdf\u7ffb\u500d\u3001\u4e0b\u884c\u88ab\u538b\u5230 512kbps\u3002\u590d\u73b0\u300c\u70b9\u4e00\u4e0b\u8981\u8f6c\u534a\u5929\u5708\u300d\u3002"});
        PRESET_META.put("satellite", new String[]{"\u536b\u661f\u94fe\u8def\uff08\u9ad8\u5ef6\u8fdf\uff09", "\u536b\u661f\u94fe\u8def", "\u5f31\u7f51\u73af\u5883", "\u5355\u7a0b 700ms\uff08\u4e00\u6b21\u5f80\u8fd4\u7ea6 1.4 \u79d2\uff09\u3001\u5e26\u5bbd 2Mbps\u3002\u5178\u578b\u8868\u73b0\u662f\u300c\u6bcf\u4e2a\u8bf7\u6c42\u90fd\u8981\u7b49\u597d\u51e0\u79d2\u624d\u56de\u6765\u300d\uff0c\u7528\u6765\u9a8c\u8bc1\u52a0\u8f7d\u6001\u3001\u8d85\u65f6\u4e0e\u91cd\u8bd5\u3002"});
        PRESET_META.put("high_loss", new String[]{"\u9ad8\u4e22\u5305\uff0830% \u4e22\u5305\uff09", "\u9ad8\u4e22\u5305", "\u5f31\u7f51\u73af\u5883", "\u5e26\u5bbd\u770b\u7740\u591f\uff08\u80fd\u8d77\u8fde\u3001\u80fd\u63e1\u624b\uff09\uff0c\u4f46\u6bcf\u4e09\u4e2a\u5305\u4e22\u4e00\u4e2a\u3002\u5178\u578b\u75c7\u72b6\uff1a\u89c6\u9891\u5361\u6210 PPT\u3001\u56fe\u7247\u52a0\u8f7d\u4e00\u534a\u3001\u957f\u8fde\u63a5\u53cd\u590d\u91cd\u8bd5\u3002"});
        PRESET_META.put("high_latency", new String[]{"\u9ad8\u5ef6\u8fdf\uff08RTT 800ms\uff09", "\u9ad8\u5ef6\u8fdf", "\u5f31\u7f51\u73af\u5883", "\u5e26\u5bbd\u6b63\u5e38\u3001\u4e5f\u4e0d\u4e22\u5305\uff0c\u7eaf\u7cb9\u5f80\u8fd4\u6162\uff08\u8de8\u5883\u94fe\u8def\u3001\u536b\u661f\u56de\u4f20\u3001\u4ee3\u7406\u4e2d\u8f6c\uff09\u3002\u590d\u73b0\u7684\u662f\u300c\u6162\u4f46\u6709\u7ed3\u679c\u300d\uff0c\u4e0d\u662f\u300c\u4f20\u4e0d\u52a8\u300d\u3002"});
        PRESET_META.put("highspeed_rail", new String[]{"\u9ad8\u94c1 / \u5730\u94c1\uff08\u9ad8\u901f\u79fb\u52a8\uff09", "\u9ad8\u94c1/\u5730\u94c1", "\u6781\u7aef\u73af\u5883", "\u9ad8\u901f\u79fb\u52a8\u4e0b\u9891\u7e41\u5207\u6362\u57fa\u7ad9\uff1a\u6296\u52a8\u6781\u5927\uff08\u00b1150ms\uff09\u3001\u5076\u53d1\u65ad\u6d41\u3001\u5e26\u5bbd\u4f4e\u3002\u9002\u5408\u9a8c\u8bc1\u91cd\u8fde\u4e0e\u65ad\u70b9\u7eed\u4f20\u3002"});
        PRESET_META.put("elevator", new String[]{"\u7535\u68af / \u5730\u4e0b\u5ba4\uff08\u4fe1\u53f7\u6781\u5dee\uff09", "\u7535\u68af/\u5730\u4e0b\u5ba4", "\u6781\u7aef\u73af\u5883", "\u4fe1\u53f7\u6781\u5dee\uff1a\u5ef6\u8fdf\u63a5\u8fd1\u79d2\u7ea7\u300120% \u4e22\u5305\u300110% \u65ad\u8fde\uff0c\u5e38\u4f34\u8d85\u65f6\u3002\u6700\u5bb9\u6613\u89e6\u53d1\u300c\u8bf7\u6c42\u5361\u6b7b\u300d\u7684\u4e00\u6863\u3002"});
        PRESET_META.put("flapping", new String[]{"\u9891\u7e41\u65ad\u8fde\uff08\u7f51\u7edc\u5207\u6362\uff09", "\u9891\u7e41\u65ad\u8fde", "\u6781\u7aef\u73af\u5883", "\u6bcf 8 \u79d2\u65ad\u7f51 3 \u79d2\uff0c\u6a21\u62df\u7f51\u7edc\u53cd\u590d\u5207\u6362 / \u5f31\u4fe1\u53f7\u6389\u7ebf\u3002\u4e13\u95e8\u7528\u6765\u9a8c\u8bc1\u91cd\u8fde\u3001\u91cd\u8bd5\u4e0e\u65ad\u7ebf\u63d0\u793a\u903b\u8f91\u3002"});
        PRESET_META.put("blackhole", new String[]{"\u9ed1\u6d1e\uff08\u8bf7\u6c42\u65e0\u54cd\u5e94\uff09", "\u9ed1\u6d1e\uff08\u65e0\u54cd\u5e94\uff09", "\u6781\u7aef\u73af\u5883", "\u8bf7\u6c42\u53d1\u51fa\u53bb\u6ca1\u6709\u4efb\u4f55\u54cd\u5e94\uff0c\u8fde\u63a5\u6302\u4f4f\u4e0d\u8fd4\u56de \u2014\u2014 \u4e0d\u662f\u62a5\u9519\uff0c\u662f\u4e00\u76f4\u7b49\u3002\u7528\u6765\u9a8c\u8bc1\u8d85\u65f6\u3001\u53d6\u6d88\u4e0e\u515c\u5e95 UI\u3002"});
        PRESET_META.put("dns_fail", new String[]{"DNS \u6545\u969c\uff08\u65e0\u6cd5\u89e3\u6790\u57df\u540d\uff09", "DNS \u6545\u969c", "\u6781\u7aef\u73af\u5883", "\u57df\u540d\u89e3\u6790\u5168\u90e8\u5931\u8d25\uff0c\u4f46\u76f4\u63a5\u7528 IP \u7684\u8fde\u63a5\u4ecd\u7136\u901a \u2014\u2014 \u53ea\u62e6 DNS \u67e5\u8be2\uff0c\u4e0d\u62e6\u5df2\u5efa\u7acb\u7684\u6570\u636e\u901a\u9053\u3002"});
        PRESET_META.put("airplane", new String[]{"\u5b8c\u5168\u65ad\u7f51\uff08\u7acb\u5373\u62d2\u8fde\uff09", "\u5b8c\u5168\u65ad\u7f51", "\u6781\u7aef\u73af\u5883", "\u6240\u6709\u65b0\u8fde\u63a5\u7acb\u5373\u88ab\u62d2\u7edd\uff08\u662f\u7acb\u523b\u5931\u8d25\uff0c\u4e0d\u662f\u6302\u4f4f\uff09\u3002\u6a21\u62df\u98de\u884c\u6a21\u5f0f / \u5b8c\u5168\u65e0\u7f51\u7edc\uff0c\u9a8c\u8bc1\u5feb\u901f\u5931\u8d25\u4e0e\u964d\u7ea7\u8def\u5f84\u3002"});
    }

    public static boolean hasPreset(String key) {
        return key != null && PRESET_META.containsKey(key);
    }

    private static String presetMeta(String key, int i) {
        String[] m = key == null ? null : PRESET_META.get(key);
        return m == null ? "" : m[i];
    }

    public static String presetName(String key) {
        return presetMeta(key, 0);
    }

    public static String presetShort(String key) {
        return presetMeta(key, 1);
    }

    public static String presetCat(String key) {
        return presetMeta(key, 2);
    }

    public static String presetNote(String key) {
        return presetMeta(key, 3);
    }

    /**
     * 把预置覆盖的参数写进 Profile。
     *
     * 直接生成赋值语句而不是 JSON：纯 JDK 单测用 android.jar 的 org.json stub，
     * 一碰 new JSONObject 就抛 Stub!，会让 Profile 的静态初始化整体失败。
     */
    public static void applyPreset(String key, Profile p) {
        if (p == null || key == null) return;
        if ("wifi".equals(key)) {
            p.latencyUpMs = 10;
            p.latencyDownMs = 30;
            p.jitterMs = 5;
            p.downKbps = 50000;
            p.upKbps = 20000;
        } else if ("4g".equals(key)) {
            p.latencyUpMs = 40;
            p.latencyDownMs = 60;
            p.jitterMs = 15;
            p.downKbps = 20000;
            p.upKbps = 10000;
        } else if ("4g_weak".equals(key)) {
            p.latencyUpMs = 70;
            p.latencyDownMs = 110;
            p.jitterMs = 45;
            p.downKbps = 3000;
            p.upKbps = 800;
            p.lossPct = 4;
            p.disconnectPct = 1;
            p.stallMs = 1200;
        } else if ("5g".equals(key)) {
            p.latencyUpMs = 12;
            p.latencyDownMs = 20;
            p.jitterMs = 8;
            p.downKbps = 100000;
            p.upKbps = 40000;
        } else if ("5g_weak".equals(key)) {
            p.latencyUpMs = 50;
            p.latencyDownMs = 80;
            p.jitterMs = 35;
            p.downKbps = 6000;
            p.upKbps = 1500;
            p.lossPct = 2;
            p.disconnectPct = 1;
            p.stallMs = 1000;
        } else if ("wifi_weak".equals(key)) {
            p.latencyUpMs = 60;
            p.latencyDownMs = 120;
            p.jitterMs = 60;
            p.downKbps = 5000;
            p.upKbps = 2000;
            p.lossPct = 3;
        } else if ("congestion".equals(key)) {
            p.latencyUpMs = 200;
            p.latencyDownMs = 400;
            p.jitterMs = 100;
            p.downKbps = 512;
            p.upKbps = 256;
            p.lossPct = 5;
        } else if ("satellite".equals(key)) {
            p.latencyUpMs = 700;
            p.latencyDownMs = 700;
            p.jitterMs = 80;
            p.downKbps = 2000;
            p.upKbps = 512;
        } else if ("high_loss".equals(key)) {
            p.latencyUpMs = 100;
            p.latencyDownMs = 150;
            p.jitterMs = 80;
            p.downKbps = 8000;
            p.upKbps = 3000;
            p.lossPct = 30;
            p.stallMs = 800;
        } else if ("high_latency".equals(key)) {
            p.latencyUpMs = 400;
            p.latencyDownMs = 400;
            p.jitterMs = 60;
            p.downKbps = 10000;
            p.upKbps = 5000;
        } else if ("highspeed_rail".equals(key)) {
            p.latencyUpMs = 60;
            p.latencyDownMs = 100;
            p.jitterMs = 150;
            p.downKbps = 1500;
            p.upKbps = 512;
            p.lossPct = 8;
            p.disconnectPct = 2;
            p.stallMs = 1200;
        } else if ("elevator".equals(key)) {
            p.latencyUpMs = 500;
            p.latencyDownMs = 800;
            p.jitterMs = 300;
            p.downKbps = 256;
            p.upKbps = 128;
            p.lossPct = 20;
            p.disconnectPct = 10;
            p.stallMs = 2000;
        } else if ("flapping".equals(key)) {
            p.latencyUpMs = 80;
            p.latencyDownMs = 120;
            p.jitterMs = 50;
            p.downKbps = 8000;
            p.upKbps = 4000;
            p.disconnectPct = 5;
            p.flapSec = 8;
            p.flapBlackoutSec = 3;
        } else if ("blackhole".equals(key)) {
            p.timeoutPct = 100;
        } else if ("dns_fail".equals(key)) {
            p.dnsFailPct = 100;
        } else if ("airplane".equals(key)) {
            p.rejectPct = 100;
        }
    }

    // ---------------------------------------------------------------- 参数表

    public static final String[] PARAM_KEYS = {"latency_down_ms", "latency_up_ms", "jitter_ms", "down_kbps", "up_kbps", "loss_pct", "stall_ms", "disconnect_pct", "timeout_pct", "dns_fail_pct", "reject_pct", "flap_sec", "flap_blackout_sec"};

    private static final String[] PARAM_LABELS = {"\u4e0b\u884c\u5ef6\u8fdf", "\u4e0a\u884c\u5ef6\u8fdf", "\u6296\u52a8 \u00b1", "\u4e0b\u884c\u5e26\u5bbd", "\u4e0a\u884c\u5e26\u5bbd", "\u4e22\u5305\u7387", "\u5361\u987f", "\u65ad\u8fde\u7387", "\u9ed1\u6d1e\u7387", "DNS\u5931\u8d25", "\u62d2\u8fde\u7387", "\u65ad\u7f51\u95f4\u9694", "\u65ad\u7f51\u65f6\u957f"};
    private static final String[] PARAM_UNITS = {"ms", "ms", "ms", "kbps", "kbps", "%", "ms", "%", "%", "%", "%", "\u79d2", "\u79d2"};
    private static final int[] PARAM_MAX = {10000, 10000, 5000, 1000000, 1000000, 100, 60000, 100, 100, 100, 100, 3600, 600};
    private static final int[] PARAM_DEFAULT = {0, 0, 0, 0, 0, 0, 600, 0, 0, 0, 0, 0, 2};

    /** /api/params 的原始条目（只在设备上解析，不参与类初始化） */
    private static final String PARAM_JSON = "[{\"key\":\"latency_down_ms\",\"label\":\"\\u4e0b\\u884c\\u5ef6\\u8fdf\",\"unit\":\"ms\",\"type\":\"int\",\"min\":0,\"max\":10000,\"default\":0,\"step\":1},{\"key\":\"latency_up_ms\",\"label\":\"\\u4e0a\\u884c\\u5ef6\\u8fdf\",\"unit\":\"ms\",\"type\":\"int\",\"min\":0,\"max\":10000,\"default\":0,\"step\":1},{\"key\":\"jitter_ms\",\"label\":\"\\u6296\\u52a8 \\u00b1\",\"unit\":\"ms\",\"type\":\"int\",\"min\":0,\"max\":5000,\"default\":0,\"step\":1},{\"key\":\"down_kbps\",\"label\":\"\\u4e0b\\u884c\\u5e26\\u5bbd\",\"unit\":\"kbps\",\"type\":\"int\",\"min\":0,\"max\":1000000,\"default\":0,\"step\":1},{\"key\":\"up_kbps\",\"label\":\"\\u4e0a\\u884c\\u5e26\\u5bbd\",\"unit\":\"kbps\",\"type\":\"int\",\"min\":0,\"max\":1000000,\"default\":0,\"step\":1},{\"key\":\"loss_pct\",\"label\":\"\\u4e22\\u5305\\u7387\",\"unit\":\"%\",\"type\":\"float\",\"min\":0,\"max\":100,\"default\":0.0,\"step\":0.1},{\"key\":\"stall_ms\",\"label\":\"\\u5361\\u987f\",\"unit\":\"ms\",\"type\":\"int\",\"min\":0,\"max\":60000,\"default\":600,\"step\":1},{\"key\":\"disconnect_pct\",\"label\":\"\\u65ad\\u8fde\\u7387\",\"unit\":\"%\",\"type\":\"float\",\"min\":0,\"max\":100,\"default\":0.0,\"step\":0.1},{\"key\":\"timeout_pct\",\"label\":\"\\u9ed1\\u6d1e\\u7387\",\"unit\":\"%\",\"type\":\"float\",\"min\":0,\"max\":100,\"default\":0.0,\"step\":0.1},{\"key\":\"dns_fail_pct\",\"label\":\"DNS\\u5931\\u8d25\",\"unit\":\"%\",\"type\":\"float\",\"min\":0,\"max\":100,\"default\":0.0,\"step\":0.1},{\"key\":\"reject_pct\",\"label\":\"\\u62d2\\u8fde\\u7387\",\"unit\":\"%\",\"type\":\"float\",\"min\":0,\"max\":100,\"default\":0.0,\"step\":0.1},{\"key\":\"flap_sec\",\"label\":\"\\u65ad\\u7f51\\u95f4\\u9694\",\"unit\":\"\\u79d2\",\"type\":\"int\",\"min\":0,\"max\":3600,\"default\":0,\"step\":1},{\"key\":\"flap_blackout_sec\",\"label\":\"\\u65ad\\u7f51\\u65f6\\u957f\",\"unit\":\"\\u79d2\",\"type\":\"int\",\"min\":0,\"max\":600,\"default\":2,\"step\":1}]";

    private static final Map<String, String> PARAM_NOTES =
            new LinkedHashMap<String, String>();
    private static final Map<String, Integer> PARAM_IDX =
            new LinkedHashMap<String, Integer>();

    static {
        PARAM_IDX.put("latency_down_ms", Integer.valueOf(0));
        PARAM_IDX.put("latency_up_ms", Integer.valueOf(1));
        PARAM_IDX.put("jitter_ms", Integer.valueOf(2));
        PARAM_IDX.put("down_kbps", Integer.valueOf(3));
        PARAM_IDX.put("up_kbps", Integer.valueOf(4));
        PARAM_IDX.put("loss_pct", Integer.valueOf(5));
        PARAM_IDX.put("stall_ms", Integer.valueOf(6));
        PARAM_IDX.put("disconnect_pct", Integer.valueOf(7));
        PARAM_IDX.put("timeout_pct", Integer.valueOf(8));
        PARAM_IDX.put("dns_fail_pct", Integer.valueOf(9));
        PARAM_IDX.put("reject_pct", Integer.valueOf(10));
        PARAM_IDX.put("flap_sec", Integer.valueOf(11));
        PARAM_IDX.put("flap_blackout_sec", Integer.valueOf(12));
        PARAM_NOTES.put("latency_down_ms", "\u7ed9\u300c\u670d\u52a1\u5668 \u2192 \u672c\u673a\u300d\u65b9\u5411\u6bcf\u5757\u6570\u636e\u52a0\u7684\u56fa\u5b9a\u5ef6\u8fdf\uff0c\u7528\u6765\u62c9\u957f\u54cd\u5e94\u56de\u6765\u7684\u65f6\u95f4\u3002\n\u6bcf\u5757\u6570\u636e\u90fd\u4f1a\u52a0\uff0c\u4e0d\u662f\u53ea\u5728\u5efa\u8fde\u65f6\u52a0\u4e00\u6b21 \u2014\u2014 \u5426\u5219 keep-alive / WebSocket \u8fd9\u7c7b\u957f\u8fde\u63a5\u91cc\u53ea\u6709\u7b2c\u4e00\u4e2a\u8bf7\u6c42\u6162\uff0c\u540e\u9762\u7684\u5168\u662f\u96f6\u5ef6\u8fdf\u3002\u4e00\u6b21\u5f80\u8fd4 \u2248 \u4e0a\u884c\u5ef6\u8fdf + \u4e0b\u884c\u5ef6\u8fdf\u3002");
        PARAM_NOTES.put("latency_up_ms", "\u7ed9\u300c\u672c\u673a \u2192 \u670d\u52a1\u5668\u300d\u65b9\u5411\u52a0\u7684\u56fa\u5b9a\u5ef6\u8fdf\uff0c\u5f71\u54cd\u8bf7\u6c42\u53d1\u51fa\u53bb\u8981\u591a\u4e45\u624d\u5230\u670d\u52a1\u5668\u3002\n\u300c\u70b9\u4e86\u6309\u94ae\u8fdf\u8fdf\u4e0d\u53d1\u8bf7\u6c42\u300d\u770b\u8fd9\u9879\uff0c\u300c\u8bf7\u6c42\u56de\u6765\u4e86\u4f46\u754c\u9762\u4e0d\u5237\u65b0\u300d\u770b\u4e0b\u884c\u5ef6\u8fdf\u3002\u4e00\u6b21\u5f80\u8fd4 \u2248 \u4e0a\u884c + \u4e0b\u884c\uff0c\u6240\u4ee5\u4e24\u9879\u90fd\u586b 400 \u65f6 RTT \u662f 800ms\u3002");
        PARAM_NOTES.put("jitter_ms", "\u6bcf\u4e2a\u6570\u636e\u5757\u7684\u5ef6\u8fdf\u5728\u57fa\u7840\u503c\u4e0a\u968f\u673a\u6d6e\u52a8 \u00b1N \u6beb\u79d2\uff0c\u8ba9\u5ef6\u8fdf\u5ffd\u5feb\u5ffd\u6162\u3002\n\u5b9e\u9645\u5ef6\u8fdf = \u57fa\u7840\u5ef6\u8fdf + (-N ~ +N) \u7684\u968f\u673a\u503c\uff1b\u6296\u52a8\u5e45\u5ea6\u4e0d\u4f1a\u8d85\u8fc7\u57fa\u7840\u5ef6\u8fdf\uff08\u5426\u5219\u4f1a\u7b97\u51fa\u8d1f\u5ef6\u8fdf\uff0c\u53d8\u6210\u6bd4\u76f4\u8fde\u8fd8\u5feb\u7684\u300c\u96f6\u5ef6\u8fdf\u5305\u300d\uff09\uff0c\u8d85\u51fa\u65f6\u6309\u57fa\u7840\u5ef6\u8fdf\u5c01\u9876\u3002\u6296\u52a8\u4e3b\u8981\u5f71\u54cd\u5b9e\u65f6\u97f3\u89c6\u9891\u3001\u6e38\u620f\u3001VoIP\uff1b\u7eaf\u4e0b\u8f7d\u51e0\u4e4e\u611f\u89c9\u4e0d\u5230\u3002");
        PARAM_NOTES.put("down_kbps", "\u4e0b\u884c\u603b\u5e26\u5bbd\u4e0a\u9650\uff0c\u5355\u4f4d kbps\uff081 Mbps = 1000 kbps\uff0c1000 kbps \u2248 125 KB/s\uff09\u30020 = \u4e0d\u9650\u3002\n\u6ce8\u610f\u662f\u5168\u5c40\u5171\u4eab\u7684\u4e00\u4efd\u5e26\u5bbd\uff1a\u540c\u65f6\u5f00 5 \u6761\u8fde\u63a5\u662f 5 \u6761\u4e00\u8d77\u5206\u644a\uff0c\u4e0d\u662f\u6bcf\u6761\u5404\u62ff\u4e00\u4efd\u3002");
        PARAM_NOTES.put("up_kbps", "\u4e0a\u884c\u603b\u5e26\u5bbd\u4e0a\u9650\uff08\u672c\u673a \u2192 \u670d\u52a1\u5668\uff09\uff0c\u5355\u4f4d kbps\u30020 = \u4e0d\u9650\u3002\n\u4e0a\u4f20\u5927\u6587\u4ef6\u3001\u53d1\u56fe\u7247\u3001\u63d0\u4ea4\u5927\u8bf7\u6c42\u4f53\u65f6\u5148\u5361\u5728\u8fd9\u91cc\uff1b\u53ea\u6d4b\u300c\u9875\u9762\u52a0\u8f7d\u6162\u300d\u65f6\u901a\u5e38\u4e0d\u7528\u6539\u5b83\u3002");
        PARAM_NOTES.put("loss_pct", "\u6bcf\u5757\u6570\u636e\u6309\u8fd9\u4e2a\u6982\u7387\u300c\u4e22\u6389\u300d\uff0c\u8868\u73b0\u4e0d\u662f\u62a5\u9519\uff0c\u800c\u662f\u4e00\u6bb5\u505c\u987f\uff08\u6a21\u62df TCP \u7b49\u91cd\u4f20\uff09\u3002\n\u505c\u591a\u4e45\u7531\u4e0b\u9762\u7684\u300c\u5361\u987f\u65f6\u957f\u300d\u51b3\u5b9a\uff0c\u4e22\u5305\u7387\u672c\u8eab\u4e0d\u51b3\u5b9a\u505c\u591a\u4e45\u3002\u586b 100 \u4f1a\u8868\u73b0\u4e3a\u5b8c\u5168\u4f20\u4e0d\u52a8\u3002");
        PARAM_NOTES.put("stall_ms", "\u6bcf\u6b21\u300c\u4e22\u5305\u300d\u547d\u4e2d\u540e\u505c\u987f\u591a\u4e45\uff0c\u5b9e\u9645\u4f1a\u5728 0.5~1.5 \u500d\u4e4b\u95f4\u968f\u673a\u3002\n\u53ea\u5728\u4e22\u5305\u7387 > 0 \u65f6\u751f\u6548 \u2014\u2014 \u4e22\u5305\u7387\u4e3a 0 \u65f6\u8fd9\u9879\u586b\u4ec0\u4e48\u90fd\u4e0d\u5f71\u54cd\u7ed3\u679c\u3002\u9ed8\u8ba4 600\u3002");
        PARAM_NOTES.put("disconnect_pct", "\u6bcf\u5757\u6570\u636e\u6309\u6982\u7387\u76f4\u63a5\u6390\u65ad\u6574\u6761\u8fde\u63a5\uff08\u4e24\u4fa7 RST/FIN\uff09\uff0cApp \u770b\u5230\u7684\u662f\u300c\u8fde\u63a5\u88ab\u91cd\u7f6e\u300d\u3002\n\u548c\u4e22\u5305\u7684\u533a\u522b\uff1a\u4e22\u5305\u662f\u6162\uff0c\u65ad\u8fde\u662f\u65ad\uff0c\u5fc5\u987b\u91cd\u8fde\u624d\u80fd\u7ee7\u7eed\u3002\u53ea\u5f71\u54cd\u5df2\u5efa\u7acb\u7684\u8fde\u63a5\uff1b\u80fd\u4e0d\u80fd\u5efa\u8fde\u7531\u300c\u62d2\u8fde\u7387\u300d\u7ba1\u3002");
        PARAM_NOTES.put("timeout_pct", "\u65b0\u8fde\u63a5\u6309\u6982\u7387\u300c\u53d1\u51fa\u53bb\u6ca1\u6709\u56de\u5e94\u300d\uff0c\u5148\u6302\u4f4f\u4e0d\u8fd4\u56de\uff0c\u8fc7\u4e00\u4f1a\u513f\u624d\u7ee7\u7eed\u5904\u7406\u3002\n\u6700\u5bb9\u6613\u89e6\u53d1\u300c\u8bf7\u6c42\u5361\u6b7b\u3001\u8f6c\u5708\u4e0d\u505c\u300d\u3002\u548c\u300c\u62d2\u8fde\u7387\u300d\u7684\u533a\u522b\uff1a\u8fd9\u4e2a\u662f\u6302\u4f4f\u7b49\uff0c\u90a3\u4e2a\u662f\u7acb\u523b\u5931\u8d25\u3002");
        PARAM_NOTES.put("dns_fail_pct", "\u6309\u6982\u7387\u8ba9\u57df\u540d\u89e3\u6790\u5931\u8d25\uff08\u8fd4\u56de 502 DNS resolution failed\uff09\u3002\n\u53ea\u62e6\u300c\u9700\u8981\u89e3\u6790\u57df\u540d\u300d\u7684\u8fde\u63a5\uff1a\u7528 IP \u76f4\u8fde\u7684\uff08HTTPDNS\u3001\u5199\u6b7b IP \u7684 App\uff09\u4e0d\u53d7\u5f71\u54cd \u2014\u2014 \u6240\u4ee5\u586b 100% \u4e5f\u53ef\u80fd\u770b\u5230\u90e8\u5206\u8bf7\u6c42\u6b63\u5e38\u3002");
        PARAM_NOTES.put("reject_pct", "\u65b0\u8fde\u63a5\u6309\u6982\u7387\u7acb\u523b\u88ab\u62d2\u7edd\uff08\u76f4\u63a5\u8fd4\u56de 502\uff09\uff0c\u662f\u300c\u9a6c\u4e0a\u5931\u8d25\u300d\u800c\u4e0d\u662f\u6302\u4f4f\u3002\n\u6a21\u62df\u98de\u884c\u6a21\u5f0f / \u5b8c\u5168\u6ca1\u7f51\uff0c\u7528\u6765\u9a8c\u8bc1 App \u7684\u5feb\u901f\u5931\u8d25\u4e0e\u964d\u7ea7\u8def\u5f84\u3002");
        PARAM_NOTES.put("flap_sec", "\u6bcf\u9694 N \u79d2\u628a\u5f53\u524d\u6240\u6709\u8fde\u63a5\u5168\u90e8\u6390\u65ad\uff0c\u6301\u7eed\u300c\u65ad\u7f51\u65f6\u957f\u300d\u540e\u6062\u590d\u30020 = \u5173\u95ed\u3002\n\u8fd9\u662f\u5468\u671f\u6027\u7684\u6574\u4f53\u65ad\u7f51\uff0c\u8ddf\u300c\u65ad\u8fde\u7387\u300d\u968f\u673a\u6390\u5355\u6761\u4e0d\u662f\u4e00\u56de\u4e8b\uff1b\u4e24\u9879\u53ef\u4ee5\u53e0\u52a0\u3002");
        PARAM_NOTES.put("flap_blackout_sec", "\u6bcf\u6b21\u5468\u671f\u65ad\u7f51\u6301\u7eed\u591a\u4e45\uff0c\u5230\u70b9\u81ea\u52a8\u6062\u590d\u3002\n\u53ea\u5728\u300c\u65ad\u7f51\u95f4\u9694\u300d> 0 \u65f6\u751f\u6548\uff0c\u5355\u72ec\u586b\u6ca1\u6709\u4f5c\u7528\u3002");
    }

    private static int paramIdx(String key) {
        Integer i = key == null ? null : PARAM_IDX.get(key);
        return i == null ? -1 : i.intValue();
    }

    public static String paramNote(String key) {
        String s = key == null ? null : PARAM_NOTES.get(key);
        return s == null ? "" : s;
    }

    public static String paramLabel(String key) {
        int i = paramIdx(key);
        return i < 0 ? "" : PARAM_LABELS[i];
    }

    public static String paramUnit(String key) {
        int i = paramIdx(key);
        return i < 0 ? "" : PARAM_UNITS[i];
    }

    public static int paramMax(String key) {
        int i = paramIdx(key);
        return i < 0 ? 0 : PARAM_MAX[i];
    }

    public static int paramDefault(String key) {
        int i = paramIdx(key);
        return i < 0 ? 0 : PARAM_DEFAULT[i];
    }

    /** 参数编辑界面的行：{key, "标签 (单位)", 默认值文本}，顺序与 spec 一致 */
    public static String[][] fields() {
        String[][] out = new String[PARAM_KEYS.length][3];
        for (int i = 0; i < PARAM_KEYS.length; i++) {
            String unit = PARAM_UNITS[i];
            out[i] = new String[]{PARAM_KEYS[i],
                    unit.length() == 0 ? PARAM_LABELS[i]
                            : PARAM_LABELS[i] + " (" + unit + ")",
                    String.valueOf(PARAM_DEFAULT[i])};
        }
        return out;
    }

    /** /api/params 的响应体：{spec_hash, items:[...]}（仅设备侧调用） */
    public static JSONObject paramsJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("spec_hash", SPEC_HASH);
            o.put("items", new JSONArray(PARAM_JSON));
        } catch (Exception e) {
            // ignore
        }
        return o;
    }
}
