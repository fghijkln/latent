import java.util.List;
import java.util.Collections;

/** Java overloads used by the Latent stage-1 interop regression tests. */
public class Stage1Overloads {
    public static String reference(Number value) { return "Number"; }
    public static String reference(Object value) { return "Object"; }

    public static String collection(List<?> value) { return "List"; }
    public static String collection(Object value) { return "Object"; }

    public static String array(int[] value) { return "int[]"; }
    public static String array(Object value) { return "Object"; }

    public static String choose(String value) { return "fixed"; }
    public static String choose(String... values) { return "varargs"; }

    public static String nullable(CharSequence value) { return "CharSequence"; }
    public static String nullable(String value) { return "String"; }

    public static String floatValue(float value) { return Float.toString(value); }
    public static String intValue(int value) { return Integer.toString(value); }
    public static String mapString(Object value) { return String.valueOf(value); }
    public static List<Object> listOf(Object value) {
        return Collections.singletonList(value);
    }

    public static String narrow(byte value) { return "byte"; }
    public static String narrow(int value) { return "int"; }

    public static String wide(int value) { return "int"; }
    public static String wide(long value) { return "long"; }

    public static String real(double value) { return "double"; }
    public static String real(float value) { return "float"; }

    public static String sameGrade(int value) { return "int"; }
    public static String sameGrade(long value) { return "long"; }

    public static String cross(Number left, Object right) { return "number-first"; }
    public static String cross(Object left, Number right) { return "number-second"; }
}
