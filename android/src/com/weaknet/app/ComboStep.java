package com.weaknet.app;

/**
 * 弱网组合里的一段：哪支场景 + 跑多久。
 *
 * 单独拆成文件是为了让 {@link ComboPlan} 能在纯 JDK 下跑单测：
 * 原来 Step 住在 MyCombos 里，而 MyCombos 引用 WeakNet / FloatWindow / SharedPreferences，
 * 整条依赖链都要 android.jar 才能编过，ComboPlan 就跟着没法在 PC 上测了。
 * 这一层是纯数据，没有任何 Android 依赖，拆出来单测就能直接跑。
 */
public class ComboStep {
    /**
     * presetKey 前缀：与内置预设、"u:&lt;id&gt;" 自定义场景区分开。
     *
     * 常量放在这一层（而不是 MyCombos 里）是为了让 ComboPlan 不必引用 MyCombos：
     * MyCombos 带着 android.jar 的依赖，ComboPlan 一旦引它就再也跑不了 PC 单测。
     */
    public static final String KEY_PREFIX = "c:";

    /** 哪支场景：内置预设 key、"u:&lt;id&gt;" 自定义场景、"c:&lt;id&gt;" 组合（组合不能嵌套，见 ComboPlan.norm） */
    public String key;
    public int minutes;
    public int seconds;

    public ComboStep() {
    }

    public ComboStep(String key, int minutes, int seconds) {
        this.key = key;
        this.minutes = minutes;
        this.seconds = seconds;
    }

    /**
     * 复制一份。
     *
     * 返回 ComboStep 而不是自类型：Java 的泛型方法没法被子类协变覆盖，
     * 与其玩那套把戏，不如让 ComboPlan.norm 用「新建 + 拷字段」的方式保持调用方类型。
     */
    public ComboStep copy() {
        return new ComboStep(key, minutes, seconds);
    }

    /**
     * 新建一个「同类型」的空实例。
     *
     * ComboPlan.norm 要保持调用方的类型（MyCombos.Step 进、MyCombos.Step 出），
     * 就得能造出同类型的实例。子类用协变返回覆盖本方法（Step blank()），
     * ComboPlan 那边强转一次：运行期拿到的确实是子类实例，
     * 后面 `for (Step s : steps)` 的 checkcast 才不会炸。
     */
    public ComboStep blank() {
        return new ComboStep();
    }
}
