package com.weaknet.app;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/**
 * 「可接管的目标」列表：Android 端按**包名**接管（VpnService 本身就支持按应用过滤），
 * 所以这里的候选就是已安装的应用。
 *
 * 控制接口 /api/targets 与手机端的「指定程序」二级界面共用这一份实现，
 * 免得两处各查一遍、结果还不一致。
 */
public final class AppList {

    public static final class Row {
        public final String id;      // 包名
        public final String name;    // 显示名

        Row(String id, String name) {
            this.id = id;
            this.name = name;
        }
    }

    private AppList() {
    }

    /**
     * 读取应用列表（按显示名排序，已排除本应用自身）。
     *
     * 首选查「启动器」intent（官方推荐写法，只列有启动入口的应用，过滤掉纯后台服务）；
     * 查不到说明被包可见性挡住了，退回枚举已安装应用——宁可多列几个后台服务，
     * 也不能给用户一张空列表（选不出 App = 功能不可用）。
     */
    public static List<Row> load(Context ctx) {
        List<Row> rows = new ArrayList<Row>();
        try {
            PackageManager pm = ctx.getPackageManager();
            Intent main = new Intent(Intent.ACTION_MAIN);
            main.addCategory(Intent.CATEGORY_LAUNCHER);
            List<ResolveInfo> ris = pm.queryIntentActivities(main, 0);
            for (ResolveInfo ri : ris) {
                String pkg = ri.activityInfo == null ? null : ri.activityInfo.packageName;
                if (pkg == null || ctx.getPackageName().equals(pkg)) continue;
                rows.add(new Row(pkg, String.valueOf(ri.loadLabel(pm))));
            }
            if (rows.isEmpty()) {
                for (ApplicationInfo ai : pm.getInstalledApplications(0)) {
                    if (ctx.getPackageName().equals(ai.packageName)) continue;
                    rows.add(new Row(ai.packageName,
                            String.valueOf(pm.getApplicationLabel(ai))));
                }
            }
        } catch (Exception e) {
            WeakNet.I.log("error", "读取应用列表失败：" + e.getMessage());
        }
        Collections.sort(rows, new Comparator<Row>() {
            public int compare(Row x, Row y) {
                return x.name.compareToIgnoreCase(y.name);
            }
        });
        return rows;
    }

    /** 包名 → 显示名（列表里没有就退回包名本身，用户至少看得见是什么） */
    public static String nameOf(List<Row> rows, String pkg) {
        if (pkg == null) return "";
        for (Row r : rows) {
            if (r.id.equals(pkg)) return r.name;
        }
        return pkg;
    }
}
