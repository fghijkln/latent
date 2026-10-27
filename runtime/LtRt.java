import java.util.*;
import java.io.*;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.MathContext;
import java.math.RoundingMode;
import java.lang.reflect.Array;
import java.lang.ref.ReferenceQueue;
import java.lang.ref.WeakReference;
import java.util.concurrent.locks.ReentrantLock;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Latent Java-backend runtime.
 *
 * Value model: every Latent value is an Object
 *   int    -> Long        (signed 64-bit)
 *   float64 -> Double
 *   str    -> String
 *   bool   -> Boolean
 *   nil    -> null
 *   list   -> ArrayList<Object>
 *   map    -> LinkedHashMap<String, Object>
 *   py handle -> PyHandle (lazy: python3 starts on first actual use)
 */
public class LtRt {
    // shortcut: cap eager ranges at 1M items; revisit when ranges become lazy.
    private static final int MAX_RANGE_ITEMS = 1_000_000;

    // ---------------- truthiness ----------------
    public static boolean truthy(Object v) {
        if (v == null) return false;
        if (v instanceof Boolean) return (Boolean) v;
        if (v instanceof Long) return (Long) v != 0L;
        if (v instanceof Double) {
            double d = (Double) v;
            return d != 0.0;
        }
        if (v instanceof String) return !((String) v).isEmpty();
        if (v instanceof List) return !((List<?>) v).isEmpty();
        if (v instanceof Map) return !((Map<?, ?>) v).isEmpty();
        if (v instanceof PyHandle) return Daemon.inst().truthy((PyHandle) v);
        return true;
    }

    static double num(Object v, String op) {
        if (v instanceof Double) return (Double) v;
        throw new TypeError("bad " + op + " operand: " + typeName(v));
    }

    static boolean isInt(Object v) { return v instanceof Long; }
    static boolean isNumber(Object v) { return isInt(v) || v instanceof Double; }

    static long addExact(long a, long b) {
        try { return Math.addExact(a, b); }
        catch (ArithmeticException e) { throw new OverflowError("int64 overflow"); }
    }

    static long subtractExact(long a, long b) {
        try { return Math.subtractExact(a, b); }
        catch (ArithmeticException e) { throw new OverflowError("int64 overflow"); }
    }

    static long multiplyExact(long a, long b) {
        try { return Math.multiplyExact(a, b); }
        catch (ArithmeticException e) { throw new OverflowError("int64 overflow"); }
    }

    static double exactDouble(Object value) {
        if (value instanceof Double) return (Double) value;
        if (value instanceof Long) {
            long integer = (Long) value;
            double result = (double) integer;
            if (new BigDecimal(result).compareTo(BigDecimal.valueOf(integer)) == 0)
                return result;
            throw new TypeError(
                "cannot mix int and float without precision loss");
        }
        throw new TypeError("number expected, got " + repr(value));
    }

    static double[] numericPair(Object a, Object b, String op) {
        if (!isNumber(a))
            throw new TypeError("bad " + op + " operand: " + typeName(a));
        if (!isNumber(b))
            throw new TypeError("bad " + op + " operand: " + typeName(b));
        return new double[] { exactDouble(a), exactDouble(b) };
    }

    static String typeName(Object v) {
        if (v == null) return "nil";
        if (v instanceof Long) return "int";
        if (v instanceof Double) return "num";
        if (v instanceof String) return "str";
        if (v instanceof Boolean) return "bool";
        if (v instanceof List) return "list";
        if (v instanceof Map) return "map";
        if (v instanceof LtFunction || v instanceof InteropBoundMethod ||
                v instanceof InteropModuleFunction ||
                v instanceof InteropStaticMethod) return "function";
        if (v instanceof PyHandle) return "py";
        if (v instanceof JReflect.JClass) return "java class";
        if (v instanceof JReflect.JObj) return "java obj";
        return v.getClass().getSimpleName();
    }

    // ---------------- arithmetic ----------------
    public static Object add(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__add__", a, b);
        if (a instanceof Long && b instanceof Long)
            return addExact((Long) a, (Long) b);
        if (isNumber(a) && isNumber(b)) {
            double[] n = numericPair(a, b, "+");
            return n[0] + n[1];
        }
        if (a instanceof Double && b instanceof Double) return (Double) a + (Double) b;
        if (a instanceof String && b instanceof String) return (String) a + (String) b;
        if (a instanceof List && b instanceof List) {
            List<Object> r = new ArrayList<>((List<?>) a);
            r.addAll((List<?>) b);
            return r;
        }
        throw new RuntimeException("bad + operands: " + repr(a) + ", " + repr(b));
    }

    public static Object sub(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__sub__", a, b);
        if (a instanceof Long && b instanceof Long)
            return subtractExact((Long) a, (Long) b);
        double[] n = numericPair(a, b, "-");
        return n[0] - n[1];
    }

    public static Object mul(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__mul__", a, b);
        if (a instanceof Long && b instanceof Long)
            return multiplyExact((Long) a, (Long) b);
        double[] n = numericPair(a, b, "*");
        return n[0] * n[1];
    }

    public static Object div(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__truediv__", a, b);
        if (!isNumber(a)) throw new TypeError("bad / operand: " + typeName(a));
        if (!isNumber(b)) throw new TypeError("bad / operand: " + typeName(b));
        double x, y;
        if (a instanceof Long && b instanceof Long) {
            x = ((Long) a).doubleValue();
            y = ((Long) b).doubleValue();
        } else {
            double[] n = numericPair(a, b, "/");
            x = n[0]; y = n[1];
        }
        if (y == 0) throw new ZeroDivisionError("division by zero");
        return x / y;
    }

    public static Object mod(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__mod__", a, b);
        if (a instanceof Long && b instanceof Long) {
            long x = (Long) a, y = (Long) b;
            if (y == 0) throw new ZeroDivisionError("division by zero");
            return Math.floorMod(x, y);
        }
        double[] n = numericPair(a, b, "%");
        double x = n[0], y = n[1];
        if (y == 0) throw new ZeroDivisionError("division by zero");
        double result = x % y;
        if (result != 0 && (result < 0) != (y < 0)) result += y;
        return result == 0 ? Math.copySign(0.0, y) : result;
    }

    public static Object pow(Object a, Object b) {
        if (a instanceof PyHandle || b instanceof PyHandle)
            return Daemon.inst().binop("__pow__", a, b);
        if (a instanceof Long && b instanceof Long) {
            long base = (Long) a, exponent = (Long) b;
            if (exponent >= 0) {
                long result = 1, factor = base, remaining = exponent;
                while (remaining != 0) {
                    if ((remaining & 1L) != 0) result = multiplyExact(result, factor);
                    remaining >>>= 1;
                    if (remaining != 0) factor = multiplyExact(factor, factor);
                }
                return result;
            }
        }
        double[] n = numericPair(a, b, "**");
        double x = n[0], y = n[1];
        if (x < 0 && Double.isFinite(y) && y != Math.rint(y))
            return Double.NaN;
        return Math.pow(x, y);
    }

    public static Object neg(Object a) {
        if (a instanceof PyHandle) return Daemon.inst().binop("__neg__", a);
        if (a instanceof Long) {
            long value = (Long) a;
            if (value == Long.MIN_VALUE) throw new OverflowError("int64 overflow");
            return -value;
        }
        return -num(a, "unary -");
    }

    // ---------------- comparison ----------------
    public static boolean eq(Object a, Object b) {
        if (a == null || b == null) return a == b;
        // remote / opaque handles: identity, matches Python backend
        if (a instanceof PyHandle || b instanceof PyHandle) return a == b;
        if (a instanceof LtObj || b instanceof LtObj) return a == b;
        if (a instanceof LtClass || b instanceof LtClass) return a == b;
        if (a instanceof LtFunction || b instanceof LtFunction) return a == b;
        if (a instanceof JReflect.JClass || b instanceof JReflect.JClass) return a == b;
        if (a instanceof JReflect.JObj || b instanceof JReflect.JObj) return a == b;
        if (isNumber(a) && isNumber(b))
            return !unordered(a, b) && compareNumeric(a, b) == 0;
        if (a instanceof String && b instanceof String) return a.equals(b);
        if (a instanceof Boolean && b instanceof Boolean) return a.equals(b);
        if (a instanceof List && b instanceof List) {
            List<?> la = (List<?>) a, lb = (List<?>) b;
            if (la.size() != lb.size()) return false;
            for (int i = 0; i < la.size(); i++)
                if (!eq(la.get(i), lb.get(i))) return false;
            return true;
        }
        if (a instanceof Map && b instanceof Map) {
            Map<?, ?> ma = (Map<?, ?>) a, mb = (Map<?, ?>) b;
            if (ma.size() != mb.size()) return false;
            for (Object k : ma.keySet())
                if (!mb.containsKey(k)) return false;
            for (Object k : ma.keySet())
                if (!eq(ma.get(k), mb.get(k))) return false;
            return true;
        }
        return false;
    }

    static int cmp(Object a, Object b, String op) {
        if (isNumber(a) && isNumber(b)) return compareNumeric(a, b);
        if (a instanceof String && b instanceof String)
            return JReflect.compareCodePoints((String) a, (String) b);
        throw new RuntimeException("bad " + op + " operands: " + typeName(a) + ", " + typeName(b));
    }

    static boolean unordered(Object a, Object b) {
        return (a instanceof Double && Double.isNaN((Double) a)) ||
            (b instanceof Double && Double.isNaN((Double) b));
    }

    static int compareNumeric(Object a, Object b) {
        if (a instanceof Long && b instanceof Long)
            return Long.compare((Long) a, (Long) b);
        if (a instanceof Double && b instanceof Double) {
            double x = (Double) a, y = (Double) b;
            return x > y ? 1 : (x < y ? -1 : 0);
        }
        BigDecimal x = a instanceof Long ? BigDecimal.valueOf((Long) a) :
            new BigDecimal((Double) a);
        BigDecimal y = b instanceof Long ? BigDecimal.valueOf((Long) b) :
            new BigDecimal((Double) b);
        return x.compareTo(y);
    }
    public static boolean lt(Object a, Object b) {
        return !unordered(a, b) && cmp(a, b, "<") < 0;
    }
    public static boolean lte(Object a, Object b) {
        return !unordered(a, b) && cmp(a, b, "<=") <= 0;
    }
    public static boolean gt(Object a, Object b) {
        return !unordered(a, b) && cmp(a, b, ">") > 0;
    }
    public static boolean gte(Object a, Object b) {
        return !unordered(a, b) && cmp(a, b, ">=") >= 0;
    }

    // ---------------- repr / say ----------------
    /** Shortest-roundtrip float formatting, Python-repr style. */
    static String numStr(double d) {
        if (Double.isNaN(d)) return "nan";
        if (Double.isInfinite(d)) return d > 0 ? "inf" : "-inf";
        if (d == Double.MIN_VALUE) return "5e-324";
        if (d == -Double.MIN_VALUE) return "-5e-324";
        if (d == Math.rint(d) && Math.abs(d) < 1e16) return Long.toString((long) d);
        double magnitude = Math.abs(d);
        BigDecimal decimal = magnitude < Double.MIN_NORMAL
            ? shortestSubnormal(magnitude)
            : new BigDecimal(Double.toString(magnitude));
        decimal = decimal.stripTrailingZeros();
        int exponent = decimal.precision() - decimal.scale() - 1;
        String sign = d < 0 ? "-" : "";
        if (exponent >= -4 && exponent < 16)
            return sign + decimal.toPlainString();
        // Not BigDecimal.toString(): it stays plain for scale 0 and exponents >= -6.
        String u = decimal.unscaledValue().toString();
        String mantissa = u.length() == 1 ? u : u.charAt(0) + "." + u.substring(1);
        String digits = Integer.toString(Math.abs(exponent));
        while (digits.length() < 2) digits = "0" + digits;
        return sign + mantissa + "e" + (exponent < 0 ? "-" : "+") + digits;
    }

    private static BigDecimal shortestSubnormal(double d) {
        BigDecimal exact = new BigDecimal(d);
        long bits = Double.doubleToRawLongBits(d);
        for (int precision = 1; precision <= 17; precision++) {
            BigDecimal candidate = exact.round(
                new MathContext(precision, RoundingMode.HALF_EVEN));
            if (Double.doubleToRawLongBits(candidate.doubleValue()) == bits)
                return candidate;
        }
        return new BigDecimal(Double.toString(d));
    }

    static String escape(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\t': sb.append("\\t"); break;
                case '\r': sb.append("\\r"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }

    public static String repr(Object v) {
        if (v == null) return "nil";
        if (v instanceof Boolean) return (Boolean) v ? "true" : "false";
        if (v instanceof Double) return numStr((Double) v);
        if (v instanceof String) return (String) v;
        if (v instanceof PyHandle) return Daemon.inst().repr((PyHandle) v);
        if (v instanceof LtClass) return "<class " + ((LtClass) v).name + ">";
        if (v instanceof LtFunction)
            return ((LtFunction) v).display;
        if (v instanceof InteropBoundMethod)
            return ((InteropBoundMethod) v).display;
        if (v instanceof InteropModuleFunction)
            return ((InteropModuleFunction) v).ref.display;
        if (v instanceof InteropStaticMethod)
            return ((InteropStaticMethod) v).display;
        if (v instanceof LtObj) return "<" + ((LtObj) v).cls.name + " object>";
        if (v instanceof JReflect.JClass || v instanceof JReflect.JObj)
            return JReflect.repr(v);
        if (v instanceof List) {
            StringBuilder sb = new StringBuilder("[");
            boolean first = true;
            for (Object x : (List<?>) v) {
                if (!first) sb.append(", ");
                sb.append(reprQ(x));
                first = false;
            }
            return sb.append("]").toString();
        }
        if (v instanceof Map) {
            StringBuilder sb = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) sb.append(", ");
                sb.append(reprQ(e.getKey())).append(": ").append(reprQ(e.getValue()));
                first = false;
            }
            return sb.append("}").toString();
        }
        return String.valueOf(v);
    }

    static String reprQ(Object v) {
        if (v instanceof String) return "\"" + escape((String) v) + "\"";
        return repr(v);
    }

    public static Object say(Object v) {
        System.out.println(repr(v));
        return null;
    }

    // ---------------- builtins ----------------
    public static Object len(Object x) {
        if (x instanceof List) return (long) ((List<?>) x).size();
        if (x instanceof Map) return (long) ((Map<?, ?>) x).size();
        if (x instanceof String) {
            String s = (String) x;
            return (long) s.codePointCount(0, s.length());
        }
        throw new RuntimeException("len() of " + typeName(x));
    }

    public static Object range(Object n) { return range(0L, n); }

    static long rangeNumber(Object value) {
        if (value instanceof Long) return (Long) value;
        if (!(value instanceof Double))
            throw new TypeError("number expected, got " + repr(value));
        double number = (Double) value;
        if (!Double.isFinite(number))
            throw new ArgumentError("range() arguments must be finite");
        try { return new BigDecimal(number).toBigInteger().longValueExact(); }
        catch (ArithmeticException outsideRange) {
            throw new ArgumentError("range() arguments outside signed 64-bit range");
        }
    }

    public static Object range(Object a, Object b) {
        long start = rangeNumber(a), stop = rangeNumber(b);
        BigInteger startBig = BigInteger.valueOf(start);
        BigInteger stopBig = BigInteger.valueOf(stop);
        BigInteger length = stopBig.subtract(startBig);
        if (length.signum() <= 0) return new ArrayList<>();
        if (length.compareTo(BigInteger.valueOf(MAX_RANGE_ITEMS)) > 0)
            throw new ArgumentError(
                "range() result too large (maximum 1000000 items)");
        int count = length.intValue();
        List<Object> r = new ArrayList<>(count);
        long value = start;
        for (int i = 0; i < count; i++, value++) r.add(value);
        return r;
    }

    public static Object strOf(Object x) { return repr(x); }

    public static Object toInt(Object x) {
        if (x instanceof Boolean) return (Boolean) x ? 1L : 0L;
        if (x instanceof Long) return x;
        if (x instanceof Double) {
            double d = (Double) x;
            if (!Double.isFinite(d))
                throw new ArgumentError("int() of " + repr(x));
            if (d < -0x1.0p63 || d >= 0x1.0p63)
                throw new OverflowError("int64 overflow");
            return new BigDecimal(d).toBigInteger().longValueExact();
        }
        if (x instanceof String) {
            String text = trimIntWhitespace((String) x);
            // BigInteger accepts any Unicode decimal digit (Character.digit);
            // Latent int() parses ASCII decimal only, matching the Python backend.
            if (!isAsciiDecimalLiteral(text))
                throw new ArgumentError("invalid literal for int()");
            try { return new BigInteger(text).longValueExact(); }
            catch (NumberFormatException e) {
                throw new ArgumentError("invalid literal for int()");
            } catch (ArithmeticException e) {
                throw new OverflowError("int64 overflow");
            }
        }
        throw new TypeError("int() of " + typeName(x));
    }

    static String trimIntWhitespace(String value) {
        int start = 0, end = value.length();
        while (start < end && isIntWhitespace(value.charAt(start))) start++;
        while (end > start && isIntWhitespace(value.charAt(end - 1))) end--;
        return value.substring(start, end);
    }

    static boolean isAsciiDecimalLiteral(String text) {
        int start = text.startsWith("+") || text.startsWith("-") ? 1 : 0;
        if (start == text.length()) return false;
        for (int i = start; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c < '0' || c > '9') return false;
        }
        return true;
    }

    static boolean isIntWhitespace(char c) {
        return c == ' ' || c == '\t' || c == '\n' || c == '\r' ||
            c == '\f' || c == '\u000b';
    }

    @SuppressWarnings("unchecked")
    public static Object push(Object xs, Object x) {
        if (!(xs instanceof List)) throw new RuntimeException("push() target must be a list");
        ((List<Object>) xs).add(x);
        return null;
    }

    public static Object keys(Object m) {
        if (!(m instanceof Map)) throw new RuntimeException("keys() of non-map");
        return new ArrayList<>(((Map<?, ?>) m).keySet());
    }

    public static Object listOf(Object... xs) {
        return new ArrayList<>(Arrays.asList(xs));
    }

    public static Object mapOf(Object... kvs) {
        LinkedHashMap<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kvs.length; i += 2) m.put((String) kvs[i], kvs[i + 1]);
        return m;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> iter(Object x) {
        if (x instanceof List) return (List<Object>) x;
        if (x instanceof String) {
            String s = (String) x;
            List<Object> r = new ArrayList<>();
            for (int i = 0; i < s.length();) {
                int cp = s.codePointAt(i);
                r.add(new String(Character.toChars(cp)));
                i += Character.charCount(cp);
            }
            return r;
        }
        if (x instanceof Map) return new ArrayList<>(((Map<?, ?>) x).keySet());
        if (x instanceof JReflect.JObj) { // java List / array
            Object o = ((JReflect.JObj) x).o;
            if (o instanceof List || o.getClass().isArray()) {
                Object w = JReflect.deepWrap(o);
                @SuppressWarnings("unchecked")
                List<Object> r = (List<Object>) w;
                return r;
            }
        }
        throw new RuntimeException("cannot iterate " + typeName(x));
    }

    // ---------------- py interop ----------------
    public static class PyHandle {
        final String module; // non-null for `py "mod"` handles
        final long id;       // remote object id, -1 for module handles
        final long session;
        PyHandle(String module) { this.module = module; this.id = -1; this.session = -1; }
        PyHandle(long id) {
            this.module = null;
            this.id = id;
            this.session = Daemon.trackHandle(this, id);
        }
        boolean isModule() { return module != null; }
    }

    static final class PyBoundMethodRef {
        final long id;
        final String display;
        PyBoundMethodRef(long id, String display) {
            this.id = id;
            this.display = display;
        }
    }

    static final class PyModuleFunctionRef {
        final long id;
        final String display;
        PyModuleFunctionRef(long id, String display) {
            this.id = id;
            this.display = display;
        }
    }

    static final class InteropBoundMethod {
        final String display;
        final JReflect.JObj javaReceiver;
        final String javaMethod;
        final PyBoundMethodRef pythonMethod;
        InteropBoundMethod(JReflect.JObj receiver, String method, String type) {
            this.display = "<bound method " + type + "." + method + ">";
            this.javaReceiver = receiver;
            this.javaMethod = method;
            this.pythonMethod = null;
        }
        InteropBoundMethod(PyBoundMethodRef method) {
            this.display = method.display;
            this.javaReceiver = null;
            this.javaMethod = null;
            this.pythonMethod = method;
        }
        Object invoke(Object[] args) {
            if (pythonMethod != null)
                return Daemon.inst().callBoundMethod(pythonMethod, args);
            return JReflect.call(javaReceiver, javaMethod, args);
        }
    }

    static final class InteropModuleFunction {
        final PyModuleFunctionRef ref;
        InteropModuleFunction(PyModuleFunctionRef ref) { this.ref = ref; }
        Object invoke(Object[] args) {
            return Daemon.inst().callModuleFunction(ref, args);
        }
    }

    static final class InteropStaticMethod {
        final JReflect.JClass owner;
        final String method;
        final String display;
        InteropStaticMethod(JReflect.JClass owner, String method) {
            this.owner = owner;
            this.method = method;
            this.display = "<static method " + owner.resolve().getName() +
                "." + method + ">";
        }
        Object invoke(Object[] args) {
            return JReflect.callStatic(owner, method, args);
        }
    }

    // ---------------- Latent classes (v0.3) ----------------
    /** A Latent method: uniform (self, args) shape so LtClass can hold
    a name -> implementation table without reflection. */
    public interface LtMethod {
        Object call(Object self, Object[] args);
    }

    /** A first-class Latent function value with an immutable lexical parent. */
    public interface LtBody {
        Object call(Object[] args);
    }
    /** Marker for a named actual argument; the value is already evaluated. */
    public static class NamedArg {
        final String name;
        final Object value;
        NamedArg(String name, Object value) {
            this.name = name;
            this.value = value;
        }
    }
    private static final class InteropCallArgs {
        final Object[] positional;
        final List<NamedArg> named;
        InteropCallArgs(Object[] positional, List<NamedArg> named) {
            this.positional = positional;
            this.named = named;
        }
    }
    public static class ArgumentError extends RuntimeException {
        ArgumentError(String message) { super(message); }
    }
    public static class UserThrownException extends RuntimeException {
        public UserThrownException(String message) { super(message); }
    }
    public static class IndexError extends RuntimeException {
        public IndexError(String message) { super(message); }
    }
    public static class TypeError extends RuntimeException {
        public TypeError(String message) { super(message); }
    }
    public static class OverflowError extends RuntimeException {
        public OverflowError(String message) { super(message); }
    }
    public static class ZeroDivisionError extends RuntimeException {
        public ZeroDivisionError(String message) { super(message); }
    }
    public static Object named(String name, Object value) {
        return new NamedArg(name, value);
    }
    private static class StarArg {
        final Object[] values;
        StarArg(Object[] values) { this.values = values; }
    }
    private static class StarStarArg {
        final LinkedHashMap<String, Object> values;
        StarStarArg(LinkedHashMap<String, Object> values) { this.values = values; }
    }
    public static Object star(Object value) {
        if (!(value instanceof List))
            throw new ArgumentError("* unpacking requires a Latent list");
        return new StarArg(((List<?>) value).toArray());
    }
    public static Object starstar(Object value) {
        if (!(value instanceof Map))
            throw new ArgumentError("** unpacking requires a Latent map");
        LinkedHashMap<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
            if (!(entry.getKey() instanceof String))
                throw new ArgumentError("** unpacking requires string keys");
            copy.put((String) entry.getKey(), entry.getValue());
        }
        return new StarStarArg(copy);
    }
    private static Object[] expandArgs(Object[] args) {
        List<Object> expanded = new ArrayList<>();
        for (Object arg : args) {
            if (arg instanceof StarArg) {
                expanded.addAll(Arrays.asList(((StarArg) arg).values));
            } else if (arg instanceof StarStarArg) {
                for (Map.Entry<String, Object> entry :
                        ((StarStarArg) arg).values.entrySet())
                    expanded.add(new NamedArg(entry.getKey(), entry.getValue()));
            } else {
                expanded.add(arg);
            }
        }
        return expanded.toArray();
    }
    public static final Object MISSING = new Object();
    private static boolean hasNamedArgs(Object[] args) {
        for (Object arg : args) if (arg instanceof NamedArg) return true;
        return false;
    }
    private static void rejectInteropArgs(Object[] args) {
        if (hasNamedArgs(args))
            throw new ArgumentError(
                "named arguments are not supported for Python/Java interop calls");
        for (Object arg : args)
            if (arg instanceof StarArg || arg instanceof StarStarArg)
                throw new ArgumentError(
                    "argument unpacking is not supported for Python/Java interop calls");
    }
    private static InteropCallArgs splitInteropArgs(Object[] args) {
        List<Object> positional = new ArrayList<>();
        List<NamedArg> named = new ArrayList<>();
        Set<String> names = new HashSet<>();
        for (Object arg : args) {
            if (arg instanceof StarArg || arg instanceof StarStarArg)
                throw new ArgumentError(
                    "argument unpacking is not supported for Python/Java interop calls");
            if (arg instanceof NamedArg) {
                NamedArg pair = (NamedArg) arg;
                if (!names.add(pair.name))
                    throw new ArgumentError("got duplicate named argument '" +
                        pair.name + "'");
                named.add(pair);
            } else positional.add(arg);
        }
        return new InteropCallArgs(positional.toArray(), named);
    }
    private static boolean containsInteropCallable(Object value) {
        return containsInteropCallable(value, new IdentityHashMap<Object, Boolean>(), 0);
    }
    private static boolean containsInteropCallable(Object value,
            IdentityHashMap<Object, Boolean> ancestors, int depth) {
        if (depth > 64)
            throw new RuntimeException("interop container nesting exceeds 64 levels");
        if (value instanceof LtFunction || value instanceof InteropBoundMethod ||
                value instanceof InteropModuleFunction ||
                value instanceof InteropStaticMethod)
            return true;
        if (value instanceof List || value instanceof Map) {
            if (ancestors.put(value, Boolean.TRUE) != null)
                throw new RuntimeException("cyclic interop containers are unsupported");
            try {
                if (value instanceof List) {
                    for (Object item : (List<?>) value)
                        if (containsInteropCallable(item, ancestors, depth + 1)) return true;
                } else {
                    for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet())
                        if (containsInteropCallable(entry.getKey(), ancestors, depth + 1) ||
                                containsInteropCallable(entry.getValue(), ancestors, depth + 1)) return true;
                }
            } finally {
                ancestors.remove(value);
            }
        }
        return false;
    }
    private static void rejectInteropCallables(Object[] args) {
        for (Object arg : args)
            if (containsInteropCallable(arg))
                throw new RuntimeException(
                    "Latent callable values cannot be passed to Python/Java interop calls");
    }
    private static Object[] bindArgs(String[] params, int requiredCount,
                                     Object[] args, String name) {
        return bindArgs(params, requiredCount, params.length, new String[0],
                        null, null, args, name);
    }
    private static Object[] bindArgs(String[] params, int requiredCount,
                                     int positionalCount,
                                     String[] requiredKeywordOnly,
                                     String restName, String extraName,
                                     Object[] rawArgs, String name) {
        Object[] args = expandArgs(rawArgs);
        if (!hasNamedArgs(args)) {
            if (args.length > positionalCount && restName == null)
                throw new ArgumentError(name + "() takes " + positionalCount +
                    " args, got " + args.length);
            if (args.length < requiredCount) {
                if (requiredCount == positionalCount)
                    throw new ArgumentError(name + "() takes " + positionalCount +
                        " args, got " + args.length);
                throw new ArgumentError(name +
                    "() missing required argument '" + params[args.length] + "'");
            }
            Object[] values = new Object[params.length +
                (restName == null ? 0 : 1) + (extraName == null ? 0 : 1)];
            Arrays.fill(values, 0, params.length, MISSING);
            System.arraycopy(args, 0, values, 0,
                             Math.min(args.length, positionalCount));
            for (String parameter : requiredKeywordOnly)
                throw new ArgumentError(name +
                    "() missing required keyword-only argument '" +
                    parameter + "'");
            int next = params.length;
            if (restName != null) {
                List<Object> rest = new ArrayList<>();
                for (int i = positionalCount; i < args.length; i++)
                    rest.add(args[i]);
                values[next++] = rest;
            }
            if (extraName != null) values[next] = new LinkedHashMap<String, Object>();
            return values;
        }
        Object[] values = new Object[params.length];
        Arrays.fill(values, MISSING);
        boolean[] supplied = new boolean[params.length];
        List<Object> restValues = new ArrayList<>();
        LinkedHashMap<String, Object> extraValues = new LinkedHashMap<>();
        Set<String> seenNames = new HashSet<>();
        int pos = 0;
        for (Object arg : args) {
            if (arg instanceof NamedArg) {
                NamedArg named = (NamedArg) arg;
                if (!seenNames.add(named.name))
                    throw new ArgumentError(name +
                        "() got duplicate named argument '" + named.name + "'");
                int index = Arrays.asList(params).indexOf(named.name);
                if (index < 0) {
                    if (extraName == null)
                        throw new ArgumentError(name +
                            "() got unexpected named argument '" + named.name + "'");
                    extraValues.put(named.name, named.value);
                } else {
                    if (supplied[index])
                        throw new ArgumentError(name +
                            "() got multiple values for argument '" + named.name + "'");
                    values[index] = named.value;
                    supplied[index] = true;
                }
            } else {
                if (pos >= positionalCount) {
                    if (restName == null)
                        throw new ArgumentError(name + "() takes " + positionalCount +
                            " args, got " + (pos + 1));
                    restValues.add(arg);
                } else {
                    values[pos] = arg;
                    supplied[pos] = true;
                }
                pos++;
            }
        }
        for (int i = 0; i < requiredCount; i++) {
            if (!supplied[i])
                throw new ArgumentError(name +
                    "() missing required argument '" + params[i] + "'");
        }
        for (String parameter : requiredKeywordOnly) {
            int index = Arrays.asList(params).indexOf(parameter);
            if (!supplied[index])
                throw new ArgumentError(name +
                    "() missing required keyword-only argument '" +
                    parameter + "'");
        }
        Object[] bound = Arrays.copyOf(values, params.length +
            (restName == null ? 0 : 1) + (extraName == null ? 0 : 1));
        int next = params.length;
        if (restName != null) bound[next++] = restValues;
        if (extraName != null) bound[next] = extraValues;
        return bound;
    }
    public static class LtFunction {
        final int arity;
        final int requiredCount;
        final int positionalCount;
        final String[] requiredKeywordOnly;
        final String name;
        final String display;
        final String[] params;
        final String restName;
        final String extraName;
        final LtBody body;
        LtFunction(String[] params, int requiredCount, String name, LtBody body) {
            this(params, requiredCount, params.length, new String[0], name,
                 "<function " + name + ">", null, null, body);
        }
        LtFunction(String[] params, int requiredCount, String name,
                   String display, LtBody body) {
            this(params, requiredCount, params.length, new String[0], name,
                 display, null, null, body);
        }
        LtFunction(String[] params, int requiredCount, String name,
                   String display, String restName, String extraName,
                   LtBody body) {
            this(params, requiredCount, params.length, new String[0], name,
                 display, restName, extraName, body);
        }
        LtFunction(String[] params, int requiredCount, int positionalCount,
                   String[] requiredKeywordOnly, String name, String display,
                   String restName, String extraName, LtBody body) {
            this.params = params.clone();
            this.arity = params.length;
            this.requiredCount = requiredCount;
            this.positionalCount = positionalCount;
            this.requiredKeywordOnly = requiredKeywordOnly.clone();
            this.name = name;
            this.display = display;
            this.restName = restName;
            this.extraName = extraName;
            this.body = body;
        }
        Object invoke(Object[] args) {
            return body.call(bindArgs(params, requiredCount, positionalCount,
                                      requiredKeywordOnly, restName, extraName,
                                      args, name));
        }
    }
    /** Per-invocation binding cells; nested functions retain this frame. */
    public static class Env {
        final Env parent;
        final Map<String, Object> values = new LinkedHashMap<>();
        public Env(Env parent, String[] names) {
            this.parent = parent;
            for (String name : names) values.put(name, null);
        }
        public void setLocal(String name, Object value) {
            if (!values.containsKey(name))
                throw new RuntimeException("unknown local binding '" + name + "'");
            values.put(name, value);
        }
        public void setEnclosing(String name, Object value) {
            for (Env frame = parent; frame != null; frame = frame.parent) {
                if (frame.values.containsKey(name)) {
                    frame.values.put(name, value);
                    return;
                }
            }
            throw new RuntimeException("unknown enclosing binding '" + name + "'");
        }
        public Object get(String name) {
            for (Env frame = this; frame != null; frame = frame.parent)
                if (frame.values.containsKey(name)) return frame.values.get(name);
            throw new RuntimeException("unknown lexical binding '" + name + "'");
        }
    }
    public static LtFunction function(String[] params, int requiredCount,
                                      String name, LtBody body) {
        return new LtFunction(params, requiredCount, name, body);
    }
    public static LtFunction function(String[] params, int requiredCount,
                                      String name, String restName,
                                      String extraName, LtBody body) {
        return new LtFunction(params, requiredCount, name,
            "<function " + name + ">", restName, extraName, body);
    }
    public static LtFunction function(String[] params, int requiredCount,
                                      int positionalCount,
                                      String[] requiredKeywordOnly,
                                      String name, String restName,
                                      String extraName, LtBody body) {
        return new LtFunction(params, requiredCount, positionalCount,
            requiredKeywordOnly, name, "<function " + name + ">",
            restName, extraName, body);
    }
    public static Object callValue(Object value, Object... args) {
        if (value instanceof LtFunction)
            return ((LtFunction) value).invoke(args);
        if (value instanceof InteropBoundMethod) {
            InteropBoundMethod method = (InteropBoundMethod) value;
            if (method.pythonMethod == null) {
                rejectInteropArgs(args);
                rejectInteropCallables(args);
            }
            return method.invoke(args);
        }
        if (value instanceof InteropModuleFunction) {
            return ((InteropModuleFunction) value).invoke(args);
        }
        if (value instanceof InteropStaticMethod) {
            rejectInteropArgs(args);
            rejectInteropCallables(args);
            return ((InteropStaticMethod) value).invoke(args);
        }
        throw new RuntimeException("call on non-function value");
    }

    public static class LtClass {
        public final String name;
        public final LtClass parent;
        public final Map<String, LtMethod> methods;
        public final Map<String, Integer> methodArities;
        public final Map<String, String[]> methodParameters;
        public final Map<String, Integer> methodRequiredCounts;
        public final Map<String, Integer> methodPositionalCounts;
        public final Map<String, String[]> methodRequiredKeywordOnly;
        public final Map<String, String> methodRestNames;
        public final Map<String, String> methodExtraNames;
        LtClass(String name, LtClass parent, Map<String, LtMethod> methods,
                Map<String, Integer> methodArities,
                Map<String, String[]> methodParameters,
                Map<String, Integer> methodRequiredCounts,
                Map<String, Integer> methodPositionalCounts,
                Map<String, String[]> methodRequiredKeywordOnly,
                Map<String, String> methodRestNames,
                Map<String, String> methodExtraNames) {
            this.name = name;
            this.parent = parent;
            this.methods = methods;
            this.methodArities = methodArities;
            this.methodParameters = methodParameters;
            this.methodRequiredCounts = methodRequiredCounts;
            this.methodPositionalCounts = methodPositionalCounts;
            this.methodRequiredKeywordOnly = methodRequiredKeywordOnly;
            this.methodRestNames = methodRestNames;
            this.methodExtraNames = methodExtraNames;
        }
    }

    public static class LtObj {
        public final LtClass cls;
        public final Map<String, Object> fields = new LinkedHashMap<>();
        LtObj(LtClass cls) { this.cls = cls; }
    }

    public static LtClass makeClass(String name, LtClass parent,
                                    String[] names, LtMethod[] methods,
                                    int[] arities, String[][] parameters,
                                    int[] requiredCounts) {
        return makeClass(name, parent, names, methods, arities, parameters,
                         requiredCounts, new String[names.length],
                         new String[names.length]);
    }

    public static LtClass makeClass(String name, LtClass parent,
                                    String[] names, LtMethod[] methods,
                                    int[] arities, String[][] parameters,
                                    int[] requiredCounts, String[] restNames,
                                    String[] extraNames) {
        int[] positionalCounts = arities.clone();
        String[][] requiredKeywordOnly = new String[names.length][0];
        return makeClass(name, parent, names, methods, arities, parameters,
                         requiredCounts, positionalCounts,
                         requiredKeywordOnly, restNames, extraNames);
    }

    public static LtClass makeClass(String name, LtClass parent,
                                    String[] names, LtMethod[] methods,
                                    int[] arities, String[][] parameters,
                                    int[] requiredCounts,
                                    int[] positionalCounts,
                                    String[][] requiredKeywordOnly,
                                    String[] restNames, String[] extraNames) {
        Map<String, LtMethod> m = new LinkedHashMap<>();
        Map<String, Integer> a = new LinkedHashMap<>();
        Map<String, String[]> p = new LinkedHashMap<>();
        Map<String, Integer> r = new LinkedHashMap<>();
        Map<String, Integer> positional = new LinkedHashMap<>();
        Map<String, String[]> keywordOnly = new LinkedHashMap<>();
        Map<String, String> rest = new LinkedHashMap<>();
        Map<String, String> extra = new LinkedHashMap<>();
        for (int i = 0; i < names.length; i++) {
            m.put(names[i], methods[i]);
            a.put(names[i], arities[i]);
            p.put(names[i], parameters[i].clone());
            r.put(names[i], requiredCounts[i]);
            positional.put(names[i], positionalCounts[i]);
            keywordOnly.put(names[i], requiredKeywordOnly[i].clone());
            rest.put(names[i], restNames[i]);
            extra.put(names[i], extraNames[i]);
        }
        return new LtClass(name, parent, m, a, p, r, positional, keywordOnly,
                           rest, extra);
    }

    static LtMethod findMethod(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            LtMethod method = c.methods.get(name);
            if (method != null) return method;
        }
        return null;
    }

    static int findMethodArity(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            Integer arity = c.methodArities.get(name);
            if (arity != null) return arity;
        }
        return -1;
    }

    static String[] findMethodParameters(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            String[] params = c.methodParameters.get(name);
            if (params != null) return params;
        }
        return new String[0];
    }

    static int findMethodRequiredCount(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            Integer required = c.methodRequiredCounts.get(name);
            if (required != null) return required;
        }
        return 0;
    }

    static int findMethodPositionalCount(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            Integer count = c.methodPositionalCounts.get(name);
            if (count != null) return count;
        }
        return 0;
    }

    static String[] findMethodRequiredKeywordOnly(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent) {
            String[] names = c.methodRequiredKeywordOnly.get(name);
            if (names != null) return names;
        }
        return new String[0];
    }

    static String findMethodRestName(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent)
            if (c.methodRestNames.containsKey(name))
                return c.methodRestNames.get(name);
        return null;
    }

    static String findMethodExtraName(LtClass cls, String name) {
        for (LtClass c = cls; c != null; c = c.parent)
            if (c.methodExtraNames.containsKey(name))
                return c.methodExtraNames.get(name);
        return null;
    }

    /** Dispatch a Latent method beginning at the current class's parent. */
    public static Object superCall(Object receiver, LtClass owner,
                                   String method, Object... args) {
        if (!(receiver instanceof LtObj) || owner == null)
            throw new RuntimeException("super call requires a Latent instance and class");
        LtClass actual = ((LtObj) receiver).cls;
        while (actual != null && actual != owner) actual = actual.parent;
        if (actual == null || owner.parent == null)
            throw new RuntimeException("super call owner is not in the instance inheritance chain");
        LtMethod target = findMethod(owner.parent, method);
        if (target == null)
            throw new RuntimeException("no parent method '" + method + "' on class " + owner.name);
        return target.call(receiver,
            bindArgs(findMethodParameters(owner.parent, method),
                     findMethodRequiredCount(owner.parent, method),
                     findMethodPositionalCount(owner.parent, method),
                     findMethodRequiredKeywordOnly(owner.parent, method),
                     findMethodRestName(owner.parent, method),
                     findMethodExtraName(owner.parent, method), args, method));
    }

    public static Object pymod(Object name) {
        if (!(name instanceof String))
            throw new RuntimeException("py module name must be a string");
        return new PyHandle((String) name); // lazy: nothing starts yet
    }

    static PyHandle asHandle(Object h) {
        if (h instanceof PyHandle) return (PyHandle) h;
        throw new RuntimeException("attribute access on non-py value");
    }

    public static Object pyget(Object h, String attr) {
        return Daemon.inst().get(asHandle(h), attr);
    }

    public static Object pycall(Object h, String attr, Object... args) {
        return Daemon.inst().call(asHandle(h), attr, args);
    }

    // ---------------- java interop ----------------
    public static Object jclass(Object name) {
        if (!(name instanceof String))
            throw new RuntimeException("java class name must be a string");
        return new JReflect.JClass((String) name); // lazy: Class.forName on first use
    }

    /** Unified attribute access: py handles and java handles. */
    public static Object wgetattr(Object h, String attr) {
        if (h instanceof PyHandle) {
            PyHandle python = (PyHandle) h;
            if (python.isModule()) {
                PyModuleFunctionRef function =
                    Daemon.inst().getModuleFunction(python, attr);
                if (function != null) return new InteropModuleFunction(function);
            }
            PyBoundMethodRef method = Daemon.inst().getBoundMethod((PyHandle) h, attr);
            if (method != null) return new InteropBoundMethod(method);
            return pyget(h, attr);
        }
        if (h instanceof LtObj) {
            LtObj o = (LtObj) h;
            Map<String, Object> f = o.fields;
            if (f.containsKey(attr)) return f.get(attr);
            LtMethod method = findMethod(o.cls, attr);
            if (method != null) {
                String[] params = findMethodParameters(o.cls, attr);
                return new LtFunction(params,
                    findMethodRequiredCount(o.cls, attr),
                    findMethodPositionalCount(o.cls, attr),
                    findMethodRequiredKeywordOnly(o.cls, attr), attr,
                    "<bound method " + o.cls.name + "." + attr + ">",
                    findMethodRestName(o.cls, attr),
                    findMethodExtraName(o.cls, attr),
                    args -> method.call(o, args));
            }
            throw new RuntimeException("no field '" + attr + "'");
        }
        if (h instanceof LtClass)
            throw new RuntimeException("class " + ((LtClass) h).name + " has no fields");
        if (h instanceof JReflect.JClass) {
            JReflect.JClass owner = (JReflect.JClass) h;
            if (JReflect.hasPublicStaticField(owner, attr))
                return JReflect.getField(owner, attr);
            if (JReflect.hasPublicStaticMethod(owner, attr))
                return new InteropStaticMethod(owner, attr);
            return JReflect.getField(owner, attr);
        }
        if (h instanceof JReflect.JObj) {
            JReflect.JObj object = (JReflect.JObj) h;
            try {
                return JReflect.getField(object, attr);
            } catch (RuntimeException e) {
                if (JReflect.hasPublicField(object, attr) ||
                        !JReflect.hasPublicInstanceMethod(object, attr)) throw e;
                return new InteropBoundMethod(object, attr,
                    object.o.getClass().getName());
            }
        }
        throw new RuntimeException("attribute access on non-handle value: " + typeName(h));
    }

    /** Unified call: py handles, java static/instance methods, C.new() constructors. */
    public static Object wcall(Object h, String attr, Object... args) {
        if (h instanceof PyHandle) {
            return pycall(h, attr, args);
        }
        if (h instanceof LtClass) {
            LtClass c = (LtClass) h;
            if (attr.equals("new")) {
                LtObj o = new LtObj(c);
                LtMethod init = findMethod(c, "init");
                String[] params = findMethodParameters(c, "init");
                if (init != null) init.call(o, bindArgs(params,
                    findMethodRequiredCount(c, "init"),
                    findMethodPositionalCount(c, "init"),
                    findMethodRequiredKeywordOnly(c, "init"),
                    findMethodRestName(c, "init"),
                    findMethodExtraName(c, "init"), args, c.name + ".new"));
                else if (args.length > 0) bindArgs(new String[0], 0, args, c.name + ".new");
                return o;
            }
            throw new RuntimeException("no class-level method '" + attr +
                "' on class " + c.name);
        }
        if (h instanceof LtObj) {
            LtObj o = (LtObj) h;
            LtMethod m = findMethod(o.cls, attr);
            if (m == null)
                throw new RuntimeException("no method '" + attr +
                    "' on " + o.cls.name);
            return m.call(o, bindArgs(findMethodParameters(o.cls, attr),
                findMethodRequiredCount(o.cls, attr),
                findMethodPositionalCount(o.cls, attr),
                findMethodRequiredKeywordOnly(o.cls, attr),
                findMethodRestName(o.cls, attr),
                findMethodExtraName(o.cls, attr), args, attr));
        }
        if (h instanceof JReflect.JClass) {
            rejectInteropArgs(args);
            rejectInteropCallables(args);
            if (attr.equals("new"))
                return JReflect.construct((JReflect.JClass) h, args);
            return JReflect.callStatic((JReflect.JClass) h, attr, args);
        }
        if (h instanceof JReflect.JObj) {
            rejectInteropArgs(args);
            rejectInteropCallables(args);
            return JReflect.call((JReflect.JObj) h, attr, args);
        }
        throw new RuntimeException("call on non-handle value: " + typeName(h));
    }

    public static Object wsetattr(Object h, String attr, Object v) {
        if (h instanceof LtObj) {
            ((LtObj) h).fields.put(attr, v);
            return null;
        }
        if (h instanceof PyHandle)
            return Daemon.inst().setattr((PyHandle) h, attr, v);
        if (h instanceof JReflect.JObj)
            return JReflect.setField((JReflect.JObj) h, attr, v);
        if (h instanceof JReflect.JClass)
            return JReflect.setField((JReflect.JClass) h, attr, v);
        throw new RuntimeException("cannot set attribute on " + typeName(h));
    }

    @SuppressWarnings("unchecked")
    public static Object wsetindex(Object o, Object k, Object v) {
        if (o instanceof PyHandle)
            return Daemon.inst().setitem((PyHandle) o, k, v);
        if (o instanceof JReflect.JObj) {
            Object u = ((JReflect.JObj) o).o;
            if (u instanceof List) {
                List<Object> l = (List<Object>) u;
                l.set(toIndex(k, l.size()), v);
                return null;
            }
            if (u instanceof Map) {
                ((Map<Object, Object>) u).put(k, v);
                return null;
            }
            throw new RuntimeException("cannot index-assign java value of type " +
                u.getClass().getName());
        }
        if (o instanceof List) {
            List<Object> l = (List<Object>) o;
            l.set(toIndex(k, l.size()), v);
            return null;
        }
        if (o instanceof Map) {
            ((Map<Object, Object>) o).put(k, v);
            return null;
        }
        throw new RuntimeException("cannot index-assign " + typeName(o));
    }

    /** Indexing: xs[i] / m[k] / s[i]. Negative indices count from the end. */
    public static Object index(Object o, Object k) {
        if (o instanceof PyHandle)
            return Daemon.inst().getitem((PyHandle) o, k);
        if (o instanceof JReflect.JObj) {
            Object u = ((JReflect.JObj) o).o;
            if (u instanceof List) {
                List<?> l = (List<?>) u;
                return JReflect.wrap(l.get(toIndex(k, l.size())));
            }
            if (u instanceof Map) {
                Map<?, ?> mp = (Map<?, ?>) u;
                if (!mp.containsKey(k))
                    throw new RuntimeException("key not found: " + repr(k));
                return JReflect.wrap(mp.get(k));
            }
            throw new RuntimeException("cannot index java value of type " +
                u.getClass().getName());
        }
        if (o instanceof List) {
            List<?> l = (List<?>) o;
            return l.get(toIndex(k, l.size()));
        }
        if (o instanceof Map) {
            Map<?, ?> mp = (Map<?, ?>) o;
            if (!mp.containsKey(k))
                throw new RuntimeException("key not found: " + repr(k));
            return mp.get(k);
        }
        if (o instanceof String) {
            String s = (String) o;
            int count = s.codePointCount(0, s.length());
            int index = toIndex(k, count);
            int start = s.offsetByCodePoints(0, index);
            int end = s.offsetByCodePoints(start, 1);
            return s.substring(start, end);
        }
        throw new RuntimeException("cannot index " + typeName(o));
    }

    static int toIndex(Object k, int n) {
        if (k instanceof Boolean || !(k instanceof Long || k instanceof Double))
            throw new TypeError("index must be an integer");
        BigInteger raw;
        if (k instanceof Long) {
            raw = BigInteger.valueOf((Long) k);
        } else {
            double d = (Double) k;
            if (!Double.isFinite(d) || d != Math.rint(d))
                throw new TypeError("index must be an integer");
            raw = new BigDecimal(d).toBigInteger();
        }
        BigInteger adjusted = raw.signum() < 0
            ? raw.add(BigInteger.valueOf(n)) : raw;
        if (adjusted.signum() < 0 || adjusted.compareTo(BigInteger.valueOf(n)) >= 0)
            throw new IndexError("index out of range: " + raw);
        return adjusted.intValue();
    }

    public static void shutdown() {
        Daemon.shutdown();
    }

    // ---------------- lazy python daemon ----------------
    static class Daemon {
        private static volatile Daemon instance;
        private static long nextSession = 1;
        private final long session = allocateSession();
        private Process proc;
        private BufferedWriter out;
        private BufferedReader in;
        private volatile boolean closed;
        private final ReentrantLock rpcLock = new ReentrantLock();
        private final Deque<Long> requestStack = new ArrayDeque<>();
        private long nextRequestId = 1;
        private long lastCallbackInvocationId = 0;
        private final AtomicLong nextCallbackToken = new AtomicLong(1);
        private static final ReferenceQueue<PyHandle> HANDLE_QUEUE = new ReferenceQueue<>();
        private static final Map<String, Integer> HANDLE_COUNTS = new HashMap<>();
        private static final Set<HandleRef> HANDLE_REFS = new HashSet<>();
        private final Set<Long> releasedHandles = new LinkedHashSet<>();
        private static final class HandleRef extends WeakReference<PyHandle> {
            final long id;
            final long session;
            HandleRef(PyHandle handle, long id, long session) {
                super(handle, HANDLE_QUEUE);
                this.id = id;
                this.session = session;
            }
        }
        private static synchronized long allocateSession() { return nextSession++; }
        private static String handleKey(long session, long id) { return session + ":" + id; }
        static long trackHandle(PyHandle handle, long id) {
            Daemon current = instance;
            if (current == null) return -1;
            synchronized (HANDLE_COUNTS) {
                HANDLE_REFS.add(new HandleRef(handle, id, current.session));
                String key = handleKey(current.session, id);
                HANDLE_COUNTS.put(key, HANDLE_COUNTS.getOrDefault(key, 0) + 1);
            }
            return current.session;
        }
        private void clearHandleTracking() {
            synchronized (HANDLE_COUNTS) {
                String prefix = session + ":";
                HANDLE_COUNTS.keySet().removeIf(key -> key.startsWith(prefix));
                HANDLE_REFS.removeIf(ref -> ref.session == session);
                releasedHandles.clear();
            }
        }

        static synchronized Daemon inst() {
            if (instance == null) {
                instance = new Daemon();
                instance.start();
            }
            return instance;
        }

        static synchronized void shutdown() {
            if (instance != null) {
                instance.close();
                instance = null;
            }
        }

        private void start() {
            String script = findScript();
            try {
                ProcessBuilder pb = new ProcessBuilder("python3", "-u", script);
                pb.redirectError(ProcessBuilder.Redirect.INHERIT);
                proc = pb.start();
                out = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), "UTF-8"));
                in = new BufferedReader(new InputStreamReader(proc.getInputStream(), "UTF-8"));
            } catch (IOException e) {
                throw new RuntimeException(
                    "cannot start python3 (needed for py ...): " + e.getMessage());
            }
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                try { Daemon.shutdown(); } catch (Exception ignored) {}
            }));
        }

        private static String findScript() {
            String p = System.getProperty("latent.pydaemon");
            if (p != null) return p;
            p = System.getenv("LATENT_PY");
            if (p != null && !p.isEmpty()) return p;
            try {
                String loc = LtRt.class.getProtectionDomain().getCodeSource()
                        .getLocation().toURI().getPath();
                File dir = new File(loc);
                if (dir.isFile()) dir = dir.getParentFile();
                File f = new File(dir, "ltpy.py");
                if (f.isFile()) return f.getAbsolutePath();
            } catch (Exception ignored) {}
            File cwd = new File("ltpy.py");
            if (cwd.isFile()) return cwd.getAbsolutePath();
            throw new RuntimeException(
                "ltpy.py not found: set LATENT_PY env or -Dlatent.pydaemon=<path>");
        }

        private void close() {
            if (closed) return;
            closed = true;
            Process child = proc;
            // Never inject quit into a stream currently owned by an active exchange.
            if (child != null && rpcLock.tryLock()) {
                try {
                    if (requestStack.isEmpty()) sendRaw("{\"op\":\"quit\"}");
                } catch (Exception ignored) {}
                finally { rpcLock.unlock(); }
            }
            if (child != null) child.destroy(); // also releases any blocked readLine
            clearHandleTracking();
            try { if (out != null) out.close(); } catch (Exception ignored) {}
            try { if (in != null) in.close(); } catch (Exception ignored) {}
            proc = null;
        }

        private void sendRaw(String s) throws IOException {
            out.write(s);
            out.write("\n");
            out.flush();
        }

        private static final class ProtocolException extends RuntimeException {
            ProtocolException(String message) { super(message); }
            ProtocolException(String message, Throwable cause) { super(message, cause); }
        }
        private static long safeId(Object value, String label, boolean allowZero) {
            if (!(value instanceof Number))
                throw new ProtocolException("invalid " + label + " ID");
            double n = ((Number) value).doubleValue();
            if (!Double.isFinite(n) || n != Math.rint(n) || n < (allowZero ? 0 : 1) ||
                    n > 9007199254740991d)
                throw new ProtocolException("invalid " + label + " ID");
            return (long) n;
        }
        private static Map<?, ?> object(Object value, String label) {
            if (!(value instanceof Map)) throw new ProtocolException("invalid " + label + " frame");
            return (Map<?, ?>) value;
        }
        private static void exactKeys(Map<?, ?> map, String label, String... keys) {
            Set<String> expected = new HashSet<>(Arrays.asList(keys));
            if (!map.keySet().equals(expected)) throw new ProtocolException("invalid " + label + " schema");
        }
        private static void validateCallbackValue(Object value, int depth) {
            if (depth > 64)
                throw new ProtocolException("invalid callback value nesting");
            if (value == null || value instanceof Boolean || value instanceof String)
                return;
            if (value instanceof Number) {
                if (!Double.isFinite(((Number) value).doubleValue()))
                    throw new ProtocolException("invalid callback numeric value");
                return;
            }
            if (value instanceof List) {
                for (Object item : (List<?>) value)
                    validateCallbackValue(item, depth + 1);
                return;
            }
            if (!(value instanceof Map))
                throw new ProtocolException("invalid callback value schema");
            Map<?, ?> envelope = (Map<?, ?>) value;
            if (envelope.size() != 1)
                throw new ProtocolException("invalid callback value schema");
            Map.Entry<?, ?> entry = envelope.entrySet().iterator().next();
            if (!(entry.getKey() instanceof String))
                throw new ProtocolException("invalid callback value schema");
            String key = (String) entry.getKey();
            Object body = entry.getValue();
            if ("__map".equals(key)) {
                if (!(body instanceof List))
                    throw new ProtocolException("invalid callback map envelope");
                for (Object rawPair : (List<?>) body) {
                    if (!(rawPair instanceof List) || ((List<?>) rawPair).size() != 2 ||
                            !(((List<?>) rawPair).get(0) instanceof String))
                        throw new ProtocolException("invalid callback map entry");
                    validateCallbackValue(((List<?>) rawPair).get(1), depth + 1);
                }
            } else if ("__ref".equals(key)) {
                safeId(body, "callback object ref", false);
            } else if ("__num".equals(key)) {
                if (!(body instanceof String) ||
                        !Arrays.asList("nan", "inf", "-inf").contains(body))
                    throw new ProtocolException("invalid callback number envelope");
            } else {
                // Callback descriptors and unknown envelopes are never callback values.
                throw new ProtocolException("invalid callback value envelope");
            }
        }
        private static Object decodeCallbackValue(Object value) {
            validateCallbackValue(value, 0);
            return decodeValue(value);
        }
        private static String callbackDescriptor(long token) {
            return "{\"__lt_callback__\":" + token + "}";
        }
        private static String encodeCallbackResult(Object value) {
            return encodeCallbackResult(value, new IdentityHashMap<Object, Boolean>(), 0);
        }
        private static String encodeCallbackResult(Object value,
                IdentityHashMap<Object, Boolean> ancestors, int depth) {
            if (depth > 64)
                throw new RuntimeException("callback result container nesting exceeds 64 levels");
            if (value instanceof LtFunction || value instanceof InteropBoundMethod ||
                    value instanceof InteropModuleFunction || value instanceof InteropStaticMethod)
                throw new RuntimeException("returning a Latent callback is unsupported");
            if (value instanceof List || value instanceof Map) {
                if (ancestors.put(value, Boolean.TRUE) != null)
                    throw new RuntimeException("cyclic callback result containers are unsupported");
                try {
                    if (value instanceof List) {
                        StringBuilder json = new StringBuilder("[");
                        boolean first = true;
                        for (Object item : (List<?>) value) {
                            if (!first) json.append(',');
                            json.append(encodeCallbackResult(item, ancestors, depth + 1));
                            first = false;
                        }
                        return json.append(']').toString();
                    }
                    StringBuilder json = new StringBuilder("{\"__map\":[");
                    boolean first = true;
                    for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                        if (!first) json.append(',');
                        // Validate keys as callback values too; the interop map
                        // representation stringifies keys, matching encodeArg.
                        encodeCallbackResult(entry.getKey(), ancestors, depth + 1);
                        json.append('[').append(Json.str(String.valueOf(entry.getKey())))
                            .append(',').append(encodeCallbackResult(
                                entry.getValue(), ancestors, depth + 1)).append(']');
                        first = false;
                    }
                    return json.append("]}").toString();
                } finally {
                    ancestors.remove(value);
                }
            }
            return encodeArg(value);
        }
        private String argsJson(Object[] args, Map<Long, LtFunction> callbacks) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < args.length; i++) {
                if (i != 0) sb.append(',');
                sb.append(callArgumentJson(args[i], callbacks));
            }
            return sb.append(']').toString();
        }
        private String kwargsJson(List<NamedArg> kwargs,
                Map<Long, LtFunction> callbacks) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < kwargs.size(); i++) {
                if (i != 0) sb.append(',');
                NamedArg pair = kwargs.get(i);
                sb.append('[').append(Json.str(pair.name)).append(',')
                    .append(callArgumentJson(pair.value, callbacks)).append(']');
            }
            return sb.append(']').toString();
        }
        private String callArgumentJson(Object arg,
                Map<Long, LtFunction> callbacks) {
            if (arg instanceof LtFunction) {
                long token = callbackToken();
                callbacks.put(token, (LtFunction) arg);
                return callbackDescriptor(token);
            }
            if (containsInteropCallable(arg))
                throw new RuntimeException("Latent callable values cannot be passed to Python/Java interop calls");
            return encodeArg(arg);
        }
        private long callbackToken() {
            long token = nextCallbackToken.getAndIncrement();
            if (token > 9007199254740991L)
                throw new RuntimeException("Python callback token limit exceeded");
            return token;
        }
        private String addReleasedRefs() {
            List<Long> refs = new ArrayList<>();
            synchronized (HANDLE_COUNTS) {
                HandleRef ref;
                while ((ref = (HandleRef) HANDLE_QUEUE.poll()) != null) {
                    HANDLE_REFS.remove(ref);
                    String key = handleKey(ref.session, ref.id);
                    Integer count = HANDLE_COUNTS.get(key);
                    if (count == null) continue;
                    if (count <= 1) {
                        HANDLE_COUNTS.remove(key);
                        if (ref.session == session) releasedHandles.add(ref.id);
                    } else HANDLE_COUNTS.put(key, count - 1);
                }
                refs.addAll(releasedHandles);
                releasedHandles.clear();
            }
            if (refs.isEmpty()) return "{\"kind\":\"request\",\"id\":%d,\"parent\":%d,\"payload\":%s}";
            StringBuilder ids = new StringBuilder("[");
            for (int i = 0; i < refs.size(); i++) {
                if (i != 0) ids.append(',');
                ids.append(refs.get(i));
            }
            ids.append(']');
            return "{\"kind\":\"request\",\"id\":%d,\"parent\":%d,\"release_refs\":" + ids + ",\"payload\":%s}";
        }
        private Object exchange(String req) {
            Map<String, Object> m = exchangeResponse(req);
            if (m.containsKey("error")) throw new RuntimeException("python error: " + m.get("error"));
            if (!m.containsKey("value")) throw new RuntimeException("invalid Python response payload");
            return decodeValue(m.get("value"));
        }
        @SuppressWarnings("unchecked")
        private Map<String, Object> exchangeResponse(String req) {
            return exchangeResponse(req, new LinkedHashMap<Long, LtFunction>());
        }
        @SuppressWarnings("unchecked")
        private Map<String, Object> exchangeResponse(String req,
                Map<Long, LtFunction> callbacks) {
            if (closed) {
                callbacks.clear();
                throw new RuntimeException("python daemon is shut down");
            }
            if (!rpcLock.tryLock()) {
                callbacks.clear();
                throw new RuntimeException("cross-thread Python RPC is unsupported");
            }
            long id = 0;
            Map<Long, Throwable> callbackErrors = new LinkedHashMap<>();
            Set<Long> callbackInvocations = new HashSet<>();
            try {
                if (requestStack.size() >= 16) throw new RuntimeException("Python RPC depth limit exceeded");
                id = nextRequestId++;
                if (id > 9007199254740991L) throw new RuntimeException("Python request ID limit exceeded");
                long parent = requestStack.isEmpty() ? 0 : requestStack.peek();
                requestStack.push(id);
                String framed = addReleasedRefs();
                sendRaw(String.format(java.util.Locale.ROOT, framed, id, parent, req));
                while (true) {
                    String line = in.readLine();
                    if (line == null) throw new RuntimeException("python daemon died");
                    Object parsed;
                    try { parsed = Json.parse(line); }
                    catch (RuntimeException malformed) {
                        throw new ProtocolException("invalid Python protocol JSON", malformed);
                    }
                    Map<?, ?> frame = object(parsed, "Python daemon");
                    Object kind = frame.get("kind");
                    if ("callback".equals(kind)) {
                        Set<String> oldCallbackKeys = new HashSet<>(Arrays.asList(
                            "kind", "id", "parent", "callback", "args"));
                        Set<String> callbackKeys = new HashSet<>(oldCallbackKeys);
                        callbackKeys.add("kwargs");
                        if (!frame.keySet().equals(oldCallbackKeys) &&
                                !frame.keySet().equals(callbackKeys))
                            throw new ProtocolException("invalid callback schema");
                        long cbid = safeId(frame.get("callback"), "callback token", false);
                        long frameId = safeId(frame.get("id"), "callback invocation", false);
                        if (safeId(frame.get("parent"), "callback parent", false) != id ||
                                !callbacks.containsKey(cbid) || !callbackInvocations.add(frameId) ||
                                frameId <= lastCallbackInvocationId)
                            throw new ProtocolException("unknown callback token, duplicate ID, or parent mismatch");
                        lastCallbackInvocationId = frameId;
                        Object rawArgs = frame.get("args");
                        if (!(rawArgs instanceof List)) throw new ProtocolException("invalid callback args schema");
                        List<Object> argv = new ArrayList<>(((List<?>) rawArgs).size());
                        for (Object rawArg : (List<?>) rawArgs)
                            argv.add(decodeCallbackValue(rawArg));
                        Object rawKwargs = frame.containsKey("kwargs")
                            ? frame.get("kwargs") : new ArrayList<Object>();
                        if (!(rawKwargs instanceof List))
                            throw new ProtocolException("invalid callback kwargs schema");
                        Set<String> keywordNames = new HashSet<>();
                        for (Object rawPair : (List<?>) rawKwargs) {
                            if (!(rawPair instanceof List) || ((List<?>) rawPair).size() != 2)
                                throw new ProtocolException("invalid callback kwargs pair");
                            List<?> pair = (List<?>) rawPair;
                            if (!(pair.get(0) instanceof String) ||
                                    !keywordNames.add((String) pair.get(0)))
                                throw new ProtocolException("invalid or duplicate callback kwargs name");
                            argv.add(new NamedArg((String) pair.get(0),
                                                  decodeCallbackValue(pair.get(1))));
                        }
                        String reply;
                        try {
                            Object result = callbacks.get(cbid).invoke(argv.toArray()); // retain normal Latent binder
                            reply = "{\"kind\":\"callback_result\",\"parent\":" + id +
                                ",\"id\":" + frameId + ",\"value\":" +
                                encodeCallbackResult(result) + "}";
                        } catch (ProtocolException fatalProtocolError) {
                            // A nested RPC protocol violation is fatal; never
                            // turn it into an ordinary host callback exception.
                            throw fatalProtocolError;
                        } catch (Throwable failure) {
                            long errorId = nextRequestId++;
                            if (errorId > 9007199254740991L)
                                throw new RuntimeException("Python callback error ID limit exceeded");
                            callbackErrors.put(errorId, failure);
                            String message = failure.getMessage();
                            if (message == null) message = "";
                            reply = "{\"kind\":\"callback_result\",\"parent\":" + id +
                                ",\"id\":" + frameId + ",\"error_id\":" + errorId +
                                ",\"error_message\":" + Json.str(message) + "}";
                        }
                        sendRaw(reply);
                        continue;
                    }
                    if (!"response".equals(kind))
                        throw new ProtocolException("response ID/direction mismatch");
                    exactKeys(frame, "response", "kind", "id", "parent", "payload");
                    if (safeId(frame.get("id"), "response", false) != id ||
                            safeId(frame.get("parent"), "response parent", true) != parent)
                        throw new ProtocolException("Python response ID/parent mismatch");
                    Map<?, ?> payload = object(frame.get("payload"), "response payload");
                    if (payload.containsKey("callback_error")) {
                        exactKeys(payload, "callback error payload", "callback_error");
                        long errorId = safeId(payload.get("callback_error"), "callback error", false);
                        Throwable original = callbackErrors.get(errorId);
                        if (original == null) throw new ProtocolException("unknown or expired callback error ID");
                        throw Daemon.<RuntimeException>throwOriginal(original);
                    }
                    if (payload.containsKey("error")) {
                        if (payload.containsKey("error_kind")) {
                            exactKeys(payload, "error payload", "error", "error_kind");
                            if (!"int64_overflow".equals(payload.get("error_kind")) ||
                                    !"int64 overflow".equals(payload.get("error")))
                                throw new ProtocolException("invalid Python error payload schema");
                            throw new OverflowError("int64 overflow");
                        }
                        exactKeys(payload, "error payload", "error");
                        if (!(payload.get("error") instanceof String))
                            throw new ProtocolException("invalid Python error payload schema");
                    } else if (payload.containsKey("value")) {
                        exactKeys(payload, "value payload", "value");
                    } else if (payload.containsKey("bound_method")) {
                        exactKeys(payload, "bound-method payload", "bound_method");
                        Object method = payload.get("bound_method");
                        if (method != null) {
                            Map<?, ?> value = object(method, "bound-method payload");
                            exactKeys(value, "bound-method value", "id", "type", "member");
                            safeId(value.get("id"), "bound method", false);
                            if (!(value.get("type") instanceof String) ||
                                    !(value.get("member") instanceof String))
                                throw new ProtocolException("invalid bound-method value schema");
                        }
                    } else if (payload.containsKey("module_function")) {
                        exactKeys(payload, "module-function payload", "module_function");
                        Object function = payload.get("module_function");
                        if (function != null) {
                            Map<?, ?> value = object(function, "module-function payload");
                            exactKeys(value, "module-function value", "id", "module", "name");
                            safeId(value.get("id"), "module function", false);
                            if (!(value.get("module") instanceof String) ||
                                    !(value.get("name") instanceof String))
                                throw new ProtocolException("invalid module-function value schema");
                        }
                    } else throw new ProtocolException("invalid Python response payload schema");
                    return (Map<String, Object>) payload;
                }
            } catch (ProtocolException e) {
                closed = true;
                if (proc != null) proc.destroy();
                clearHandleTracking();
                throw e;
            } catch (IOException e) {
                throw new RuntimeException("python daemon io error: " + e.getMessage());
            } finally {
                callbacks.clear();
                callbackErrors.clear();
                callbackInvocations.clear();
                if (!requestStack.isEmpty() && requestStack.peek() == id) requestStack.pop();
                rpcLock.unlock();
            }
        }
        @SuppressWarnings("unchecked")
        private static <T extends Throwable> RuntimeException throwOriginal(Throwable failure) throws T {
            throw (T) failure;
        }

        private static long currentSession() {
            Daemon current = instance;
            return current == null ? -1 : current.session;
        }

        private static String targetJson(PyHandle h) {
            if (h.isModule()) return "{\"mod\":" + Json.str(h.module) + "}";
            if (h.session != currentSession())
                throw new RuntimeException("stale Python object handle after daemon shutdown");
            return "{\"id\":" + h.id + "}";
        }

        Object get(PyHandle h, String attr) {
            return exchange("{\"op\":\"get\",\"target\":" + targetJson(h) +
                            ",\"attr\":" + Json.str(attr) + "}");
        }

        PyModuleFunctionRef getModuleFunction(PyHandle h, String attr) {
            Map<String, Object> response = exchangeResponse(
                "{\"op\":\"get_module_function\",\"target\":" +
                targetJson(h) + ",\"attr\":" + Json.str(attr) + "}");
            if (response.containsKey("error"))
                throw new RuntimeException("python error: " + response.get("error"));
            Object raw = response.get("module_function");
            if (!(raw instanceof Map)) return null;
            Map<?, ?> function = (Map<?, ?>) raw;
            long id = ((Number) function.get("id")).longValue();
            String display = "<module function " + function.get("module") +
                "." + function.get("name") + ">";
            return new PyModuleFunctionRef(id, display);
        }

        PyBoundMethodRef getBoundMethod(PyHandle h, String attr) {
            Map<String, Object> response = exchangeResponse(
                "{\"op\":\"get_bound_method\",\"target\":" + targetJson(h) +
                ",\"attr\":" + Json.str(attr) + "}");
            if (response.containsKey("error"))
                throw new RuntimeException("python error: " + response.get("error"));
            Object raw = response.get("bound_method");
            if (!(raw instanceof Map)) return null;
            Map<?, ?> method = (Map<?, ?>) raw;
            long id = ((Number) method.get("id")).longValue();
            String display = "<bound method " + method.get("type") + "." +
                method.get("member") + ">";
            return new PyBoundMethodRef(id, display);
        }

        Object callBoundMethod(PyBoundMethodRef method, Object[] args) {
            return callArgs("{\"op\":\"call_bound_method\",\"id\":" + method.id, args);
        }
        Object callModuleFunction(PyModuleFunctionRef function, Object[] args) {
            return callArgs("{\"op\":\"call_module_function\",\"id\":" + function.id, args);
        }
        Object call(PyHandle h, String attr, Object[] args) {
            return callArgs("{\"op\":\"call\",\"target\":" + targetJson(h) +
                ",\"attr\":" + Json.str(attr), args);
        }
        private Object callArgs(String prefix, Object[] args) {
            InteropCallArgs separated = splitInteropArgs(args);
            Map<Long, LtFunction> callbacks = new LinkedHashMap<>();
            String req = prefix + ",\"args\":" +
                argsJson(separated.positional, callbacks) + ",\"kwargs\":" +
                kwargsJson(separated.named, callbacks) + "}";
            Map<String, Object> response = exchangeResponse(req, callbacks);
            if (response.containsKey("error")) throw new RuntimeException("python error: " + response.get("error"));
            if (!response.containsKey("value")) throw new RuntimeException("invalid Python call response");
            return decodeValue(response.get("value"));
        }

        String repr(PyHandle h) {
            Object r = exchange("{\"op\":\"repr\",\"target\":" + targetJson(h) + "}");
            return r == null ? "nil" : String.valueOf(r);
        }

        Object getitem(PyHandle h, Object key) {
            return exchange("{\"op\":\"getitem\",\"target\":" + targetJson(h) +
                ",\"key\":" + encodeArg(key) + "}");
        }

        Object setattr(PyHandle h, String attr, Object v) {
            return exchange("{\"op\":\"setattr\",\"target\":" + targetJson(h) +
                ",\"attr\":" + Json.str(attr) + ",\"value\":" + encodeArg(v) + "}");
        }

        Object setitem(PyHandle h, Object key, Object v) {
            return exchange("{\"op\":\"setitem\",\"target\":" + targetJson(h) +
                ",\"key\":" + encodeArg(key) + ",\"value\":" + encodeArg(v) + "}");
        }

        Object binop(String dunder, Object... args) {
            StringBuilder sb = new StringBuilder(
                "{\"op\":\"binop\",\"name\":" + Json.str(dunder) + ",\"args\":[");
            for (int i = 0; i < args.length; i++) {
                if (i > 0) sb.append(",");
                sb.append(encodeArg(args[i]));
            }
            return exchange(sb.append("]}").toString());
        }

        boolean truthy(PyHandle h) {
            Object r = exchange("{\"op\":\"truthy\",\"target\":" + targetJson(h) + "}");
            return r instanceof Boolean && (Boolean) r;
        }

        private static String encodeArg(Object v) {
            if (containsInteropCallable(v))
                throw new RuntimeException(
                    "Latent callable values cannot be passed to Python/Java interop calls");
            if (v instanceof PyHandle) {
                PyHandle h = (PyHandle) v;
                if (!h.isModule() && h.session != currentSession())
                    throw new RuntimeException("stale Python object handle after daemon shutdown");
                return h.isModule() ? "{\"__mod\":" + Json.str(h.module) + "}"
                                    : "{\"__ref\":" + h.id + "}";
            }
            if (v instanceof List) {
                StringBuilder sb = new StringBuilder("[");
                boolean first = true;
                for (Object x : (List<?>) v) {
                    if (!first) sb.append(",");
                    sb.append(encodeArg(x));
                    first = false;
                }
                return sb.append("]").toString();
            }
            if (v instanceof Map) {
                StringBuilder sb = new StringBuilder("{\"__map\":[");
                boolean first = true;
                for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                    if (!first) sb.append(",");
                    sb.append("[").append(Json.str(String.valueOf(e.getKey())))
                      .append(",").append(encodeArg(e.getValue()))
                      .append("]");
                    first = false;
                }
                return sb.append("]}").toString();
            }
            return Json.encode(v);
        }

        @SuppressWarnings("unchecked")
        private static Object decodeValue(Object v) {
            if (v instanceof Map) {
                Map<String, Object> m = (Map<String, Object>) v;
                if (m.size() == 1 && m.containsKey("__map")) {
                    Object rawEntries = m.get("__map");
                    if (!(rawEntries instanceof List))
                        throw new RuntimeException("invalid Python map envelope");
                    LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                    for (Object rawPair : (List<?>) rawEntries) {
                        if (!(rawPair instanceof List) ||
                                ((List<?>) rawPair).size() != 2)
                            throw new RuntimeException("invalid Python map entry");
                        List<?> pair = (List<?>) rawPair;
                        result.put(String.valueOf(pair.get(0)),
                                   decodeValue(pair.get(1)));
                    }
                    return result;
                }
                if (m.size() == 1 && m.containsKey("__ref"))
                    return new PyHandle(((Number) m.get("__ref")).longValue());
                if (m.size() == 1 && m.containsKey("__num")) {
                    String k = (String) m.get("__num");
                    if (k.equals("nan")) return Double.NaN;
                    if (k.equals("inf")) return Double.POSITIVE_INFINITY;
                    return Double.NEGATIVE_INFINITY;
                }
                LinkedHashMap<String, Object> r = new LinkedHashMap<>();
                for (Map.Entry<String, Object> e : m.entrySet())
                    r.put(e.getKey(), decodeValue(e.getValue()));
                return r;
            }
            if (v instanceof List) {
                List<Object> r = new ArrayList<>();
                for (Object x : (List<?>) v) r.add(decodeValue(x));
                return r;
            }
            return v; // Double, String, Boolean, null
        }
    }

    // ---------------- minimal JSON ----------------
    static class Json {
        static String str(String s) {
            StringBuilder sb = new StringBuilder("\"");
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\t': sb.append("\\t"); break;
                    case '\r': sb.append("\\r"); break;
                    default:
                        if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            return sb.append("\"").toString();
        }

        static String encode(Object v) {
            if (v == null) return "null";
            if (v instanceof Boolean) return v.toString();
            if (v instanceof Long) return v.toString();
            if (v instanceof Double) {
                double d = (Double) v;
                if (Double.isNaN(d)) return "{\"__num\":\"nan\"}";
                if (Double.isInfinite(d))
                    return "{\"__num\":\"" + (d > 0 ? "inf" : "-inf") + "\"}";
                if (d == 0.0 && Double.doubleToRawLongBits(d) < 0)
                    return "-0.0";
                return numStr(d);
            }
            if (v instanceof String) return str((String) v);
            if (v instanceof List) {
                StringBuilder sb = new StringBuilder("[");
                boolean first = true;
                for (Object x : (List<?>) v) {
                    if (!first) sb.append(",");
                    sb.append(encode(x));
                    first = false;
                }
                return sb.append("]").toString();
            }
            if (v instanceof Map) {
                StringBuilder sb = new StringBuilder("{");
                boolean first = true;
                for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                    if (!first) sb.append(",");
                    sb.append(str(String.valueOf(e.getKey()))).append(":")
                      .append(encode(e.getValue()));
                    first = false;
                }
                return sb.append("}").toString();
            }
            throw new RuntimeException("cannot send to python: " + typeName(v));
        }

        static Object parse(String s) { return new P(s).value(); }

        static class P {
            final String s;
            int i;
            P(String s) { this.s = s; }
            void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }
            Object value() {
                ws();
                if (i >= s.length()) throw new RuntimeException("bad json");
                char c = s.charAt(i);
                if (c == '{') return obj();
                if (c == '[') return arr();
                if (c == '"') return string();
                if (c == 't') { i += 4; return Boolean.TRUE; }
                if (c == 'f') { i += 5; return Boolean.FALSE; }
                if (c == 'n') { i += 4; return null; }
                return number();
            }
            Map<String, Object> obj() {
                LinkedHashMap<String, Object> m = new LinkedHashMap<>();
                i++; // {
                ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws();
                    String k = string();
                    ws();
                    i++; // :
                    Object v = value();
                    if (m.containsKey(k)) throw new RuntimeException("duplicate JSON object key");
                    m.put(k, v);
                    ws();
                    char c = s.charAt(i++);
                    if (c == '}') return m;
                }
            }
            List<Object> arr() {
                List<Object> l = new ArrayList<>();
                i++; // [
                ws();
                if (s.charAt(i) == ']') { i++; return l; }
                while (true) {
                    l.add(value());
                    ws();
                    char c = s.charAt(i++);
                    if (c == ']') return l;
                }
            }
            String string() {
                StringBuilder sb = new StringBuilder();
                i++; // "
                while (true) {
                    char c = s.charAt(i++);
                    if (c == '"') return sb.toString();
                    if (c == '\\') {
                        char e = s.charAt(i++);
                        switch (e) {
                            case '"': sb.append('"'); break;
                            case '\\': sb.append('\\'); break;
                            case '/': sb.append('/'); break;
                            case 'n': sb.append('\n'); break;
                            case 't': sb.append('\t'); break;
                            case 'r': sb.append('\r'); break;
                            case 'u':
                                sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                                i += 4;
                                break;
                            default: sb.append(e);
                        }
                    } else sb.append(c);
                }
            }
            Object number() {
                int j = i;
                while (j < s.length() && "-+0123456789.eE".indexOf(s.charAt(j)) >= 0) j++;
                String raw = s.substring(i, j);
                i = j;
                if (raw.indexOf('.') < 0 && raw.indexOf('e') < 0 && raw.indexOf('E') < 0) {
                    try { return Long.parseLong(raw); }
                    catch (NumberFormatException e) {
                        throw new IllegalArgumentException(
                            "JSON integer outside signed 64-bit range");
                    }
                }
                return Double.parseDouble(raw);
            }
        }
    }
}
