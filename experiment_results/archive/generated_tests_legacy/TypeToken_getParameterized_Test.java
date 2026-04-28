package com.google.gson.reflect;

import com.google.gson.internal.$Gson$Types;
import com.google.gson.reflect.TypeToken;
import org.junit.Test;
import java.lang.reflect.Type;
import java.util.List;
import java.util.Map;
import static org.junit.Assert.*;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public class TypeToken_getParameterized_Test {

    @Test
    public void testGetParameterizedWithList() {
        Type listType = TypeToken.getParameterized(List.class, String.class).getType();
        assertTrue(listType instanceof java.lang.reflect.ParameterizedType);
        ParameterizedType parameterizedType = (ParameterizedType) listType;
        assertEquals(List.class, parameterizedType.getRawType());
        assertEquals(1, parameterizedType.getActualTypeArguments().length);
        assertEquals(String.class, parameterizedType.getActualTypeArguments()[0]);
    }

    @Test
    public void testGetParameterizedWithMap() {
        Type mapType = TypeToken.getParameterized(Map.class, String.class, Integer.class).getType();
        assertTrue(mapType instanceof java.lang.reflect.ParameterizedType);
        ParameterizedType parameterizedType = (ParameterizedType) mapType;
        assertEquals(Map.class, parameterizedType.getRawType());
        assertEquals(2, parameterizedType.getActualTypeArguments().length);
        assertEquals(String.class, parameterizedType.getActualTypeArguments()[0]);
        assertEquals(Integer.class, parameterizedType.getActualTypeArguments()[1]);
    }

    @Test
    public void testGetParameterizedWithRawTypeNotClass() {
        try {
            TypeToken.getParameterized($Gson$Types.newParameterizedTypeWithOwner(null, List.class, String.class), String.class);
            fail("Should have thrown IllegalArgumentException");
        } catch (IllegalArgumentException e) {
            assertTrue(e.getMessage().contains("rawType must be a Class"));
        }
    }

    @Test
    public void testGetParameterizedWithWrongNumberOfArguments() {
        try {
            TypeToken.getParameterized(List.class, String.class, Integer.class);
            fail("Should have thrown IllegalArgumentException");
        } catch (IllegalArgumentException e) {
            assertTrue(e.getMessage().contains("requires 1 type arguments, but got 2"));
        }
    }

    @Test
    public void testGetParameterizedWithZeroArguments() {
        Type listType = TypeToken.getParameterized(List.class).getType();
        assertTrue(listType instanceof java.lang.reflect.ParameterizedType);
        ParameterizedType parameterizedType = (ParameterizedType) listType;
        assertEquals(List.class, parameterizedType.getRawType());
        assertEquals(0, parameterizedType.getActualTypeArguments().length);
    }

    @Test
    public void testGetParameterizedWithNullRawType() {
        try {
            TypeToken.getParameterized(null, String.class);
            fail("Should have thrown NullPointerException");
        } catch (NullPointerException e) {
            // Expected
        }
    }

    @Test
    public void testGetParameterizedWithNullTypeArguments() {
        try {
            TypeToken.getParameterized(List.class, (Type[]) null);
            fail("Should have thrown NullPointerException");
        } catch (NullPointerException e) {
            // Expected
        }
    }

    @Test
    public void testGetParameterizedReturnsCorrectTypeToken() {
        TypeToken<?> typeToken = TypeToken.getParameterized(List.class, String.class);
        assertNotNull(typeToken);
        assertEquals(List.class, typeToken.getRawType());
        assertEquals(List.class, typeToken.getType());
    }

    @Test
    public void testGetParameterizedEqualsAndHashCode() {
        TypeToken<?> token1 = TypeToken.getParameterized(List.class, String.class);
        TypeToken<?> token2 = TypeToken.getParameterized(List.class, String.class);
        TypeToken<?> token3 = TypeToken.getParameterized(Map.class, String.class, Integer.class);

        assertEquals(token1, token2);
        assertNotEquals(token1, token3);
        assertEquals(token1.hashCode(), token2.hashCode());
        assertNotEquals(token1.hashCode(), token3.hashCode());
    }

    @Test
    public void testGetParameterizedToString() {
        TypeToken<?> token = TypeToken.getParameterized(List.class, String.class);
        String toStringResult = token.toString();
        assertNotNull(toStringResult);
        assertTrue(toStringResult.contains("List"));
        assertTrue(toStringResult.contains("String"));
    }
}