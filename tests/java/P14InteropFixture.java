import java.util.List;

public class P14InteropFixture {
    public static final String collision = "field";
    public static final Object opaque = new CallableValue();

    public static String classify(String value) {
        return "text:" + value;
    }

    public static String classify(double value) {
        return "number:" + value;
    }

    public static String collision(String value) {
        return "collision:" + value;
    }

    public static String accept(Object value) {
        return "accepted";
    }

    public static String acceptList(List<?> values) {
        return "accepted:" + values.size();
    }

    public static class CallableValue {
        public String call() {
            return "callable object";
        }
    }

}

class P14InteropParent {
    public static String inherited(String value) {
        return "inherited:" + value;
    }
}

class P14InteropChild extends P14InteropParent {}
