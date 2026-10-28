package com.weaknet.app;

import android.animation.ValueAnimator;
import android.content.Context;
import android.util.AttributeSet;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.widget.FrameLayout;

/**
 * 一行「左滑露出删除按钮」的内容行。
 *
 * 平时右侧只留一个很小的 ✕（省地方、也不抢眼），
 * 左滑时整行内容右移、底下露出一个完整的红色「删除」块 ——
 * 手指不用精确戳中那个小 ✕，删一段也就一下的事。
 *
 * 竖着划不动它：横向位移明显大于纵向时才接管，纵向滑动照旧给外层 ScrollView 滚列表。
 */
public class SwipeDeleteRow extends FrameLayout {

    public interface OnDeleteListener {
        void onDelete();
    }

    /** 某一行开始滑动时通知外面：用来把别的行收回去，别同时开着好几行 */
    public interface OnSlideListener {
        void onSlideStart(SwipeDeleteRow row);
    }

    private FrameLayout contentWrap;
    private View deleteView;
    private OnDeleteListener onDelete;
    private OnSlideListener onSlide;

    private final int deleteWidth;
    private final int slop;

    private float downX, downY;
    private float startOffset;
    private boolean tracking;
    private boolean open;
    private ValueAnimator anim;

    public SwipeDeleteRow(Context c) {
        this(c, null);
    }

    public SwipeDeleteRow(Context c, AttributeSet a) {
        super(c, a);
        deleteWidth = dp(48);
        slop = ViewConfiguration.get(c).getScaledTouchSlop();
    }

    /** 底层：右对齐的删除块（左滑才露出来） */
    public void setDeleteView(View v) {
        deleteView = v;
        // 抬到 0：Button 默认带 elevation，会被 Android 按 Z 顺序画到内容层上面，
        // 平时就把「秒」「✕」盖住了。删按钮必须待在底下。
        v.setElevation(0f);
        // 平时完全透明：它只在左滑时才淡入，平时一点痕迹都不留
        v.setAlpha(0f);
        addView(v, new FrameLayout.LayoutParams(deleteWidth, dp(34),
                Gravity.END | Gravity.CENTER_VERTICAL));
    }

    /** 内容层的底色：得是不透明的，否则底下的红色删除块会从字缝里透出来 */
    public void setContentBackground(int color) {
        if (contentWrap != null) contentWrap.setBackgroundColor(color);
    }

    /** 上层：真正显示的内容，整层可以左右平移 */
    public void setContentView(View v) {
        contentWrap = new FrameLayout(getContext());
        contentWrap.addView(v, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        addView(contentWrap, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        // 内容层压过删除块，保证平时完全盖住它
        contentWrap.setElevation(8f);
    }

    public void setOnDeleteListener(OnDeleteListener l) {
        onDelete = l;
    }

    public void setOnSlideListener(OnSlideListener l) {
        onSlide = l;
    }

    /** 把这一行收回原位（别的行滑开时调用） */
    public void close() {
        if (contentWrap != null && contentWrap.getTranslationX() != 0) {
            animateTo(false);
        }
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    @Override
    public boolean onInterceptTouchEvent(MotionEvent ev) {
        int action = ev.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            downX = ev.getX();
            downY = ev.getY();
            tracking = false;
            startOffset = contentWrap == null ? 0 : contentWrap.getTranslationX();
        } else if (action == MotionEvent.ACTION_MOVE) {
            float dx = ev.getX() - downX;
            float dy = ev.getY() - downY;
            // 只有明显的横向位移才接管；竖着划还是给外层 ScrollView 滚列表
            if (!tracking && Math.abs(dx) > slop && Math.abs(dx) > Math.abs(dy) * 1.2f) {
                tracking = true;
                if (onSlide != null) onSlide.onSlideStart(this);
                return true;
            }
        }
        return false;
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        if (!tracking || contentWrap == null) return super.onTouchEvent(ev);
        int action = ev.getActionMasked();
        if (action == MotionEvent.ACTION_MOVE) {
            float off = startOffset + (ev.getX() - downX);
            if (off > 0) off = 0;
            if (off < -deleteWidth) off = -deleteWidth;
            applyOffset(off);
            return true;
        }
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            tracking = false;
            animateTo(contentWrap.getTranslationX() < -deleteWidth / 2f);
            return true;
        }
        return true;
    }

    /** 内容层右移多少，删除块就淡入多少 —— 一开始滑它就冒头 */
    private void applyOffset(float off) {
        contentWrap.setTranslationX(off);
        if (deleteView == null) return;
        float a = -off / (float) deleteWidth;
        if (a < 0f) a = 0f;
        if (a > 1f) a = 1f;
        deleteView.setAlpha(a);
    }

    /** 松手回弹：滑过一半就停在打开位，没过就弹回去 */
    private void animateTo(final boolean toOpen) {
        if (anim != null) anim.cancel();
        final float from = contentWrap.getTranslationX();
        final float to = toOpen ? -deleteWidth : 0;
        open = toOpen;
        anim = ValueAnimator.ofFloat(from, to);
        anim.setDuration(160);
        anim.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            public void onAnimationUpdate(ValueAnimator ani) {
                applyOffset(((Float) ani.getAnimatedValue()).floatValue());
            }
        });
        anim.start();
    }
}
