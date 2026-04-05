package com.google.gson.internal.bind.util;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.text.ParseException;
import java.text.ParsePosition;
import java.util.Calendar;
import java.util.Date;
import java.util.GregorianCalendar;
import java.util.Locale;
import java.util.TimeZone;

import org.junit.Test;

public class ISO8601Utils_format_Test {

    @Test
    public void testFormatWithMillisAndUTC() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        String formatted = ISO8601Utils.format(date, true, TimeZone.getTimeZone("UTC"));
        assertEquals("2021-01-01T00:00:00.000Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithoutMillisAndUTC() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        String formatted = ISO8601Utils.format(date, false, TimeZone.getTimeZone("UTC"));
        assertEquals("2021-01-01T00:00:00Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithMillisAndNonUTC() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        TimeZone tz = TimeZone.getTimeZone("America/New_York");
        String formatted = ISO8601Utils.format(date, true, tz);
        
        // Should include timezone offset
        assertTrue(formatted.startsWith("2021-01-01T00:00:00.000"));
        assertTrue(formatted.contains("-") || formatted.contains("+"));
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithoutMillisAndNonUTC() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        TimeZone tz = TimeZone.getTimeZone("America/New_York");
        String formatted = ISO8601Utils.format(date, false, tz);
        
        // Should include timezone offset
        assertTrue(formatted.startsWith("2021-01-01T00:00:00"));
        assertTrue(formatted.contains("-") || formatted.contains("+"));
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithLeapYear() throws ParseException {
        Date date = new Date(1582934400000L); // 2020-02-29 00:00:00 UTC (leap year)
        String formatted = ISO8601Utils.format(date, true, TimeZone.getTimeZone("UTC"));
        assertEquals("2020-02-29T00:00:00.000Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithMidnight() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        String formatted = ISO8601Utils.format(date, true, TimeZone.getTimeZone("UTC"));
        assertEquals("2021-01-01T00:00:00.000Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithEndOfDay() throws ParseException {
        Date date = new Date(1609545599000L); // 2021-01-01 23:59:59 UTC
        String formatted = ISO8601Utils.format(date, true, TimeZone.getTimeZone("UTC"));
        assertEquals("2021-01-01T23:59:59.000Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithVariousTimeZones() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        
        // Test different timezones
        TimeZone[] timeZones = {
            TimeZone.getTimeZone("UTC"),
            TimeZone.getTimeZone("GMT"),
            TimeZone.getTimeZone("Europe/London"),
            TimeZone.getTimeZone("America/Los_Angeles"),
            TimeZone.getTimeZone("Asia/Tokyo")
        };
        
        for (TimeZone tz : timeZones) {
            String formatted = ISO8601Utils.format(date, true, tz);
            // Should contain a timezone offset or Z
            assertTrue(formatted.contains("Z") || formatted.contains("+") || formatted.contains("-"));
            
            // Verify it can be parsed back
            Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
            assertEquals(date, parsed);
        }
    }

    @Test
    public void testFormatWithMilliseconds() throws ParseException {
        Date date = new Date(1609459200123L); // 2021-01-01 00:00:00.123 UTC
        String formatted = ISO8601Utils.format(date, true, TimeZone.getTimeZone("UTC"));
        assertEquals("2021-01-01T00:00:00.123Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }

    @Test
    public void testFormatWithZeroMilliseconds() throws ParseException {
        Date date = new Date(1609459200000L); // 2021-01-01 00:00:00 UTC
        String formatted = ISO8601Utils.format(date, true, TimeZone.getTimeZone("UTC"));
        assertEquals("2021-01-01T00:00:00.000Z", formatted);
        
        // Verify it can be parsed back
        Date parsed = ISO8601Utils.parse(formatted, new ParsePosition(0));
        assertEquals(date, parsed);
    }
}